import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib.resources import files

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable

from qbot.config import Settings

SessionKey = tuple[str, str]
Sender = Callable[[str], Awaitable[None]]


@dataclass
class Session:
    messages: list[BaseMessage] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    identity_context: str = ""


class ChatService:
    def __init__(self, settings: Settings, model: Runnable | None, system_prompt: str | None = None):
        self.settings = settings
        prompt_text = system_prompt or files("qbot").joinpath("prompts/system.txt").read_text("utf-8")
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=prompt_text), MessagesPlaceholder("identity", optional=True),
            MessagesPlaceholder("history"), ("human", "{input}"),
        ])
        self.prompt = prompt
        self.chain = prompt | model | StrOutputParser() if model is not None else None
        self.sessions: OrderedDict[SessionKey, Session] = OrderedDict()
        # v0.1 使用单个处理通道；锁覆盖生成和发送，保证回复与历史顺序一致。
        self.lock = asyncio.Lock()

    async def reply(self, key: SessionKey, text: str, sender: Sender | None = None, *, model: Runnable | None = None,
                    identity_context: str = "") -> str:
        async with self.lock:
            now = time.monotonic()
            for expired in [k for k, v in self.sessions.items()
                            if now - v.touched >= self.settings.session_ttl_seconds]:
                del self.sessions[expired]
            text = text.strip()
            session = self.sessions.get(key)
            # 绑定变更后丢弃旧身份下的上下文，避免继续沿用以前的家庭称呼。
            if session and session.identity_context != identity_context:
                self.sessions.pop(key, None)
                session = None
            if text == "/reset":
                answer = "当前会话已清空。"
                if sender:
                    await sender(answer)
                self.sessions.pop(key, None)
                return answer
            if not text:
                answer = "请发送文字问题，目前仅支持文本对话。"
            elif len(text) > self.settings.max_input_chars:
                answer = f"消息过长，请控制在 {self.settings.max_input_chars} 字以内。"
            else:
                history = session.messages if session else []
                chain = self.prompt | model | StrOutputParser() if model is not None else self.chain
                if chain is None:
                    raise ValueError("尚未配置模型")
                async with asyncio.timeout(self.settings.chat_timeout_seconds):
                    answer = await chain.ainvoke({"history": history, "input": text,
                        "identity": [SystemMessage(content=identity_context)] if identity_context else []})
                answer = answer.strip()
                if not answer:
                    raise ValueError("模型返回了空文本")
                if len(answer) > self.settings.max_reply_chars:
                    answer = answer[:self.settings.max_reply_chars - 8] + "\n（内容已截断）"
                if sender:
                    await sender(answer)
                # 发送成功后再写历史，避免下一轮引用用户未收到的回复。
                if session is None:
                    if len(self.sessions) >= self.settings.session_max_count:
                        self.sessions.popitem(last=False)
                    session = Session(identity_context=identity_context)
                    self.sessions[key] = session
                session.messages = (history + [HumanMessage(text), AIMessage(answer)])[
                    -2 * self.settings.session_max_turns:
                ]
                session.touched = time.monotonic()
                self.sessions.move_to_end(key)
                return answer
            if sender:
                await sender(answer)
            return answer
