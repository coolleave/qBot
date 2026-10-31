"""单次网页通话：识别、生成、播放确认及中断生命周期。"""
import asyncio
import base64
import json
import logging
import re
import time
from importlib.resources import files

from fastapi import WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from qbot.model import provider_model
from qbot.token_usage import extract_token_usage
from qbot.voice_call_audio import send_event, stream_speech
from qbot.voice_call_config import realtime_endpoint

logger = logging.getLogger(__name__)


class VoiceProviderError(RuntimeError):
    """仅携带已脱敏的供应商错误摘要。"""


class VoiceCall:
    def __init__(self, ws: WebSocket, db, settings, call_id: str, bundle: dict, authorized):
        self.ws, self.db, self.settings = ws, db, settings
        self.id, self.bundle, self.authorized = call_id, bundle, authorized
        self.config = bundle["config"]
        self.stop = asyncio.Event()
        self.reason = "用户挂断"
        self.started = self.activity = time.monotonic()
        self.last_ping = self.started
        self.speaking_since = None
        self.turns = []
        self.current = None
        self.turn_task = None
        self.turn_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.persist_lock = asyncio.Lock()
        self.ack = asyncio.Event()
        self.expected_ack = None
        self.seen_items = set()
        self.status = "connecting"

    async def emit(self, kind: str, **values) -> None:
        async with self.send_lock:
            async with asyncio.timeout(5):
                await self.ws.send_json({"type": kind, "call_id": self.id, **values})

    async def persist(self, *, ended: bool = False) -> None:
        async with self.persist_lock:
            # 快照在锁内创建，避免异步线程看到正在变化的播放确认列表。
            snapshot = json.loads(json.dumps(self.turns, ensure_ascii=False))
            await self.db.call("update_voice_call", self.id, snapshot, self.status,
                               self.reason if ended else None, ended)

    async def interrupt(self) -> None:
        async with self.turn_lock:
            await self._interrupt()

    async def _interrupt(self) -> None:
        if self.current and self.current["status"] in ("generating", "playing"):
            self.current["status"] = "interrupted"
        self.expected_ack = None
        # 先让浏览器停播，不能等待模型连接关闭后才通知打断。
        await self.emit("interrupted", turn_id=self.current["turn_id"] if self.current else 0)
        if self.turn_task:
            self.turn_task.cancel()
            await asyncio.gather(self.turn_task, return_exceptions=True)
            self.turn_task = None
        await self.persist()

    async def start_turn(self, text: str) -> None:
        async with self.turn_lock:
            await self._interrupt()
            if len(self.turns) >= 100:
                self.reason = "已达到单次通话100轮上限"
                self.stop.set()
                return
            if len(text) > self.settings.max_input_chars:
                await self.emit("notice", message="这段话过长，请分成短句重说")
                return
            turn = {"turn_id": len(self.turns) + 1, "user": text, "answer": "", "played": [],
                    "status": "generating", "usage": {}, "error": None}
            self.turns.append(turn)
            self.current = turn
            self.activity = time.monotonic()
            await self.persist()
            await self.emit("transcript", turn_id=turn["turn_id"], text=text)
            self.turn_task = asyncio.create_task(self.respond(turn))
            self.turn_task.add_done_callback(self.turn_finished)

    def turn_finished(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("通话轮次异常结束：%s", type(task.exception()).__name__)
            self.reason = "通话轮次或记录保存失败"
            self.status = "failed"
            self.stop.set()

    def messages(self, turn: dict) -> list:
        prompt = self.bundle["bot"].get("system_prompt") or files("qbot").joinpath("prompts/system.txt").read_text("utf-8")
        messages = [SystemMessage(prompt), SystemMessage(files("qbot").joinpath("prompts/voice_call.txt").read_text("utf-8"))]
        for previous in self.turns[-11:-1]:
            messages.append(HumanMessage(previous["user"]))
            heard = "".join(previous["played"])
            if previous["status"] != "completed":
                heard += "（上一轮回复已中断或失败，仅以上片段确认播放）"
            if heard:
                messages.append(AIMessage(heard))
        messages.append(HumanMessage(turn["user"]))
        return messages

    async def respond(self, turn: dict) -> None:
        model = None
        jobs = []
        try:
            model = provider_model(self.settings.model_copy(update={"model_max_retries": 0,
                "model_max_tokens": min(self.settings.model_max_tokens, 512), "model_timeout_seconds": 45}), self.bundle["chat"])
            sentences = asyncio.Queue(maxsize=8)

            async def generate() -> None:
                buffer, aggregate = "", None
                stream = model.astream(self.messages(turn))
                try:
                    async for chunk in stream:
                        aggregate = chunk if aggregate is None else aggregate + chunk
                        delta = StrOutputParser().invoke(chunk)
                        remaining = 600 - len(turn["answer"])
                        delta = delta[:remaining]
                        turn["answer"] += delta
                        buffer += delta
                        if delta:
                            await self.emit("answer", turn_id=turn["turn_id"], text=delta)
                        while buffer:
                            match = re.search(r"[。！？!?；;\n]", buffer)
                            cut = match.end() if match and match.end() <= 100 else (100 if len(buffer) >= 100 else 0)
                            if not cut:
                                break
                            sentence, buffer = buffer[:cut].strip(), buffer[cut:]
                            if sentence:
                                await sentences.put(sentence)
                        if len(turn["answer"]) >= 600:
                            break
                    if buffer.strip():
                        await sentences.put(buffer.strip())
                    if not turn["answer"].strip():
                        raise ValueError("聊天模型未返回可朗读内容")
                finally:
                    await stream.aclose()
                    if aggregate is not None:
                        turn["usage"] = extract_token_usage(aggregate)
                await sentences.put(None)

            async def speak() -> None:
                sentence_id = 0
                while (sentence := await sentences.get()) is not None:
                    sentence_id += 1
                    if sentence_id > 16:
                        raise ValueError("本轮回复短句过多，请缩短问题")
                    # 客户端逐句确认，限制待播积压并保存真正已播完的文本。
                    self.ack.clear()
                    self.expected_ack = (turn["turn_id"], sentence_id)
                    turn["status"] = "playing"
                    await self.emit("sentence", turn_id=turn["turn_id"], sentence_id=sentence_id, text=sentence)
                    stream = stream_speech(self.bundle["tts"], sentence, self.config["voice"])
                    audio_started, sent_bytes = None, 0
                    try:
                        async for pcm in stream:
                            if audio_started is None:
                                audio_started = time.monotonic()
                            # 最多提前两秒送达，避免高速合成淹没网页播放队列。
                            delay = sent_bytes / 48000 - (time.monotonic() - audio_started) - 2
                            if delay > 0:
                                await asyncio.sleep(delay)
                            await self.emit("audio", turn_id=turn["turn_id"], sentence_id=sentence_id,
                                            pcm=base64.b64encode(pcm).decode())
                            sent_bytes += len(pcm)
                    finally:
                        await stream.aclose()
                    await self.emit("sentence_end", turn_id=turn["turn_id"], sentence_id=sentence_id)
                    async with asyncio.timeout(40):
                        await self.ack.wait()
                    turn["played"].append(sentence)
                    self.activity = time.monotonic()
                    await self.persist()
                turn["status"] = "completed"
                self.expected_ack = None
                await self.emit("turn_end", turn_id=turn["turn_id"])

            async with asyncio.timeout(120):
                jobs = [asyncio.create_task(generate()), asyncio.create_task(speak())]
                await asyncio.gather(*jobs)
        except asyncio.CancelledError:
            if turn["status"] != "completed":
                turn["status"] = "interrupted"
            raise
        except Exception as exc:
            logger.warning("网页通话回复失败：%s", type(exc).__name__)
            turn["status"] = "failed"
            turn["error"] = "回复或语音生成失败，请检查供应商配置、网络和额度"
            await self.emit("turn_error", turn_id=turn["turn_id"], message=turn["error"])
        finally:
            for task in jobs:
                task.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if model:
                try:
                    async with asyncio.timeout(5):
                        await model.root_async_client.close()
                        await asyncio.to_thread(model.root_client.close)
                except Exception as exc:
                    logger.warning("通话模型连接释放失败：%s", type(exc).__name__)
            await self.persist()

    async def receive_browser(self, upstream) -> None:
        window = time.monotonic()
        byte_count = controls = 0
        while True:
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                self.reason = "网页连接已断开"
                return
            now = time.monotonic()
            if now - window >= 1:
                window, byte_count, controls = now, 0, 0
            if message.get("bytes") is not None:
                pcm = message["bytes"]
                byte_count += len(pcm)
                if not pcm or len(pcm) > 6400 or len(pcm) % 2 or byte_count > 96000:
                    raise ValueError("麦克风音频格式或发送速率异常")
                await send_event(upstream, "input_audio_buffer.append", audio=base64.b64encode(pcm).decode())
            else:
                raw = message.get("text") or ""
                controls += 1
                if len(raw) > 1024 or controls > 20:
                    raise ValueError("通话控制消息超过限制")
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "hangup":
                    return
                if kind == "ping":
                    self.last_ping = now
                    await self.emit("pong")
                elif kind == "interrupt":
                    await self.interrupt()
                elif kind == "played" and self.expected_ack == (event.get("turn_id"), event.get("sentence_id")):
                    self.ack.set()
                elif kind == "played":
                    await self.played(event)

    async def played(self, event: dict) -> None:
        """实时模式覆盖此入口，按响应记录播放完成。"""

    async def session_config(self) -> dict:
        return {"input_audio_format": "pcm", "sample_rate": 16000,
                "input_audio_transcription": {"language": "zh"},
                "turn_detection": {"type": "server_vad", "threshold": 0.0, "silence_duration_ms": 600}}

    def background_jobs(self) -> list:
        return []

    def provider_error(self, event: dict) -> VoiceProviderError:
        error = event.get("error")
        if not isinstance(error, dict):
            return VoiceProviderError("供应商返回错误，但未提供具体说明")
        detail = " · ".join(str(error.get(key) or "") for key in ("code", "message"))
        # 先脱敏再截断，避免截断后的密钥片段无法匹配。
        for kind in ("realtime", "asr", "chat", "tts"):
            key = (self.bundle.get(kind) or {}).get("api_key")
            if key:
                detail = detail.replace(key, "[已隐藏密钥]")
        detail = re.sub(r"(?i)Bearer\s+\S+|sk-[A-Za-z0-9_-]+", "[已隐藏密钥]", detail)
        return VoiceProviderError("供应商返回错误：" + " ".join(detail.split())[:300])

    async def receive_asr(self, upstream) -> None:
        async for raw in upstream:
            event = json.loads(raw)
            kind = event.get("type")
            if kind in ("error", "conversation.item.input_audio_transcription.failed"):
                raise self.provider_error(event)
            if kind == "input_audio_buffer.speech_started":
                self.activity = self.speaking_since = time.monotonic()
                await self.interrupt()
                await self.emit("listening")
            elif kind == "input_audio_buffer.speech_stopped":
                self.speaking_since = None
                self.activity = time.monotonic()
            elif kind == "conversation.item.input_audio_transcription.text":
                await self.emit("partial", text=(event.get("text", "") + event.get("stash", ""))[:2000])
            elif kind == "conversation.item.input_audio_transcription.completed":
                item_id = event.get("item_id")
                if item_id and item_id in self.seen_items:
                    continue
                self.seen_items.add(item_id)
                if len(self.seen_items) > 200:
                    raise ValueError("识别轮次超过限制")
                text = event.get("transcript", "").strip()
                if text:
                    await self.start_turn(text)
                else:
                    await self.emit("notice", message="刚才没有识别到内容，请再说一次")
        raise ValueError("实时识别连接提前结束")

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            if not await self.authorized():
                self.reason = "登录已失效，请重新登录"
            elif now - self.started >= self.config["max_seconds"]:
                self.reason = "已达到本次通话时长上限"
            elif now - self.last_ping > 25:
                self.reason = "网页心跳超时"
            elif self.speaking_since and now - self.speaking_since > 60:
                self.reason = "连续语音超过60秒，请重新通话并分句说话"
            elif (not self.turn_task or self.turn_task.done()) and now - self.activity >= self.config["idle_seconds"]:
                self.reason = "长时间没有说话，通话已结束"
            else:
                continue
            self.stop.set()
            return

    async def run(self) -> None:
        jobs = []
        stage = "连接语音供应商"
        try:
            asr = self.bundle.get("realtime") or self.bundle["asr"]
            async with connect(realtime_endpoint(asr), additional_headers={"Authorization": "Bearer " + asr["api_key"]},
                               open_timeout=10, close_timeout=2, max_size=1_000_000, max_queue=8) as upstream:
                self.upstream = upstream
                stage = "配置语音会话"
                await send_event(upstream, "session.update", session=await self.session_config())
                async with asyncio.timeout(15):
                    while True:
                        event = json.loads(await upstream.recv())
                        if event.get("type") == "session.updated":
                            break
                        if event.get("type") == "error":
                            raise self.provider_error(event)
                self.status = "active"
                await self.persist()
                await self.emit("ready", sample_rate=16000, output_rate=24000)
                stage = "语音收发"
                jobs = [asyncio.create_task(self.receive_browser(upstream)), asyncio.create_task(self.receive_asr(upstream)),
                        asyncio.create_task(self.watchdog()), asyncio.create_task(self.stop.wait())]
                jobs.extend(asyncio.create_task(job) for job in self.background_jobs())
                done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except (WebSocketDisconnect, asyncio.CancelledError):
            self.reason = "通话连接已关闭"
        except Exception as exc:
            self.status = "failed"
            if isinstance(exc, VoiceProviderError):
                detail = str(exc)
            elif isinstance(exc, InvalidStatus):
                detail = f"供应商拒绝连接（HTTP {exc.response.status_code}），请检查密钥、地域及模型权限"
            elif isinstance(exc, ConnectionClosed):
                code = exc.rcvd.code if exc.rcvd else "未收到关闭帧"
                detail = f"供应商连接已关闭（{code}），请检查供应商服务状态和网络"
            elif isinstance(exc, TimeoutError):
                detail = "等待供应商响应超时"
            else:
                detail = f"处理异常（{type(exc).__name__}），请检查供应商配置、音频格式和网络"
            self.reason = f"{stage}失败：{detail}"
            logger.warning("网页通话终止 call_id=%s：%s", self.id, self.reason)
        finally:
            for task in jobs:
                task.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if self.turn_task:
                self.turn_task.cancel()
                await asyncio.gather(self.turn_task, return_exceptions=True)
            for turn in self.turns:
                if turn["status"] in ("generating", "playing"):
                    turn["status"] = "interrupted"
            if self.status != "failed":
                self.status = "ended"
            try:
                await self.persist(ended=True)
            except Exception as exc:
                logger.warning("通话结束记录保存失败：%s", type(exc).__name__)
                self.reason += "；结束记录保存失败"
            try:
                await self.emit("ended", message=self.reason)
                await self.ws.close()
            except (RuntimeError, WebSocketDisconnect, OSError):
                logger.debug("通话客户端已离线")
