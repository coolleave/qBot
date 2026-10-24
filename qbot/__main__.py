import argparse
import asyncio
import logging
import getpass
import secrets

import uvicorn

from qbot.chat import ChatService
from qbot.config import Settings
from qbot.model import create_model


async def local_chat(settings: Settings) -> None:
    engine = None
    if settings.admin_enabled:
        from qbot.storage import Store, mysql_engine
        from qbot.recording import RecordedChat
        settings.require_admin()
        engine = mysql_engine(settings)
        store = await asyncio.to_thread(Store, engine, settings.provider_encryption_key.get_secret_value())
        chat = RecordedChat(settings, store)
    else:
        chat = ChatService(settings, create_model(settings))
    try:
        await chat_loop(chat)
    finally:
        if engine:
            await asyncio.to_thread(engine.dispose)


async def chat_loop(chat) -> None:
    print("qBot 本地对话：/reset 清空会话，/exit 退出。调用模型会产生 API 费用。")
    while True:
        try:
            text = await asyncio.to_thread(input, "你 > ")
        except EOFError:
            return
        if text.strip() == "/exit":
            return
        try:
            print("qBot >", await chat.reply(("local", "default"), text))
        except Exception as exc:
            print(f"调用失败（{type(exc).__name__}），请检查配置、网络或账户额度。")


def main() -> None:
    parser = argparse.ArgumentParser(description="qBot：LangChain + DeepSeek")
    parser.add_argument("mode", choices=["chat", "serve", "hash-password", "generate-secrets"], help="聊天、服务或本地配置辅助工具")
    args = parser.parse_args()
    if args.mode == "generate-secrets":
        from cryptography.fernet import Fernet
        print("ADMIN_SESSION_SECRET=" + secrets.token_urlsafe(48))
        print("PROVIDER_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
        return
    if args.mode == "hash-password":
        from qbot.security import password_hash
        password = getpass.getpass("管理员密码（至少 12 字符）：")
        if password != getpass.getpass("再次输入："):
            parser.exit(2, "两次密码不一致\n")
        try:
            print("ADMIN_PASSWORD_HASH=" + password_hash(password))
        except ValueError as exc:
            parser.exit(2, str(exc) + "\n")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        settings = Settings()
        if settings.admin_enabled:
            settings.require_admin()
        else:
            settings.require_credentials(qq=args.mode == "serve")
        if args.mode == "chat":
            asyncio.run(local_chat(settings))
        else:
            from qbot.server import create_app

            uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1)
    except ValueError as exc:
        parser.exit(2, f"配置错误：{exc}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
