import os
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
EXTERNAL_BASE_URL = os.environ.get("EXTERNAL_BASE_URL") or ""
WEBAPP_URL = (EXTERNAL_BASE_URL.rstrip("/") + "/app") if EXTERNAL_BASE_URL else "/app"

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
dp = Dispatcher()


def build_open_keyboard(label: str = "Open", url: str = WEBAPP_URL) -> InlineKeyboardMarkup:
    """构建一个包含单个“Open”按钮的内联键盘，点击后在 Telegram 内打开 MiniApp。
    优先使用 WebApp 按钮，确保在聊天栏展示“Open”交互。
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))]]
    )

@dp.message(CommandStart())
async def cmd_start(message: Message):
    # 解析 /start payload（深度链接携带的参数），形如 "/start abc123"
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else None

    kb = build_open_keyboard(label="打开 PMSAuto 应用", url=WEBAPP_URL)

    # 为当前会话设置菜单按钮为 WebApp，标题为“女助理”，点击直接打开 MiniApp
    try:
        await message.bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(text="女助理", web_app=WebAppInfo(url=WEBAPP_URL)),
        )
    except Exception:
        # 忽略非关键错误，仍继续回复欢迎消息
        pass

    intro = [
        "欢迎使用 PMSAuto 机器人！",
        "",
        "可用命令：",
        "/start - 欢迎与深链",
        "/help - 查看帮助",
        "/points - 查询积分（开发中）",
        "/register - 账号注册（开发中）",
        "",
        "也可以点击下方按钮打开 MiniApp。",
    ]
    if payload:
        intro.append("")
        intro.append(f"检测到深链参数：{payload}")

    await message.answer("\n".join(intro), reply_markup=kb)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "使用指引：\n"
        "- 通过 /register 在 MiniApp 中完成注册与绑定\n"
        "- 通过 /points 查看积分与到期信息（开发中）\n"
        "- 如遇问题，点击菜单打开 MiniApp 获取更多操作"
    )

@dp.message(Command("points"))
async def cmd_points(message: Message):
    # TODO: 调用后台 /admin/api/me 或根据 tg_id 查询
    await message.answer("暂未绑定账户，或积分功能开发中。")

@dp.message(Command("register"))
async def cmd_register(message: Message):
    # TODO: 引导到 MiniApp 页面进行注册/绑定
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="打开注册/绑定", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )
    await message.answer("请点击下方按钮打开 MiniApp 完成注册与绑定。", reply_markup=kb)


@dp.message(Command("open"))
async def cmd_open(message: Message):
    """发送一个包含“Open”按钮的消息。支持 /open <可选文案|payload>。
    - 文案：显示在消息正文，默认“打开 MiniApp”。
    - payload：如需跳转到特定子页，可在 WEBAPP_URL 后自定义参数（前端根据哈希/查询参数处理）。
    用法示例：/open 去个人信息页#home
    """
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    suffix = parts[1] if len(parts) > 1 else ""
    body = suffix or "打开 MiniApp"
    # 如果用户给了简单页签，如 #home / #admin，则拼到 URL；否则原样当成提示文案
    url = WEBAPP_URL
    if suffix.startswith("#") or suffix.startswith("?"):
        url = f"{WEBAPP_URL}{suffix}"
    kb = build_open_keyboard(label="Open", url=url)
    await message.answer(body, reply_markup=kb)
