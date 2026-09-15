"""
自定义指令注册器

在 AstrBot WebUI 的插件配置中以「键值对」维护自定义指令：
键 = 指令名，值 = 固定回复（字符串），
或用 json 类型的值同时指定回复、描述与别名：
{"reply": "回复内容", "desc": "指令描述", "aliases": "别名1,别名2"}

插件加载时把每条指令真正注册进 AstrBot 的指令系统：

- 聊天中发送 /指令 即回复固定内容，全程不经过 LLM
- 「QQ菜单面板管理」插件的指令选择器可以读到这些指令，
  可一键同步到 QQ 官方机器人的 "/" 指令面板

注意：修改插件配置后，需要在 WebUI 中重载本插件（或重启 AstrBot）才会生效。
"""

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_custom_commands"

# 动态指令的优先级。略高于普通插件 handler（默认 0），
# 保证命中自定义指令后优先回复，并通过 stop_event 阻断后续流程（包括 LLM）
COMMAND_PRIORITY = 50


@register(
    PLUGIN_NAME,
    "机智的小琥珀",
    "在 WebUI 中自定义注册指令并固定回复，可被 QQ 菜单面板插件读取同步",
    "1.0.1",
)
class CustomCommandsPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self._registered: list[str] = []  # 本次加载注册成功的指令名，用于日志与查询

    async def initialize(self):
        """插件加载 / 重载时由框架调用：根据当前配置动态注册指令"""
        self._registered.clear()
        for index, item in enumerate(self._get_command_items()):
            self._register_one(index, item)
        if self._registered:
            logger.info(
                f"[{PLUGIN_NAME}] 已注册 {len(self._registered)} 条自定义指令: "
                + ", ".join("/" + name for name in self._registered)
            )
        else:
            logger.info(
                f"[{PLUGIN_NAME}] 暂无自定义指令，请在 WebUI 的插件配置中添加"
            )

    # ------------------------------------------------------------------
    # 配置读取与校验
    # ------------------------------------------------------------------
    def _get_command_items(self) -> list[dict]:
        """读取配置中的指令表，统一解析成 [{command, reply, desc, aliases}, ...]

        配置结构为 dict：键 = 指令名，值有两种写法——
        · 字符串：直接作为回复内容
        · dict（WebUI 编辑器中类型选 json）：{"reply": ..., "desc": ..., "aliases": ...}
        同时兼容手工把配置写成 list[dict] 的情况。
        """
        try:
            raw = self.config.get("commands", {})
        except Exception:
            return []

        items: list[dict] = []
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("command", key)
                else:
                    item = {"command": key, "reply": str(value)}
                items.append(item)
        elif isinstance(raw, list):
            # 兼容直接手改配置文件写成列表的情况
            items = [it for it in raw if isinstance(it, dict)]
        else:
            logger.warning(f"[{PLUGIN_NAME}] 配置项 commands 结构无法识别，已忽略")
        return items

    def _register_one(self, index: int, item: dict):
        """把一条配置注册成一条真正的 AstrBot 指令"""
        command = str(item.get("command", "")).strip().lstrip("/").strip()
        reply = str(item.get("reply", ""))
        desc = str(item.get("desc", "")).strip() or f"自定义指令 /{command}"
        alias_raw = str(item.get("aliases", "")).strip()
        aliases = {
            a.strip().lstrip("/")
            for a in alias_raw.replace("，", ",").split(",")
            if a.strip()
        }

        if not command:
            logger.warning(f"[{PLUGIN_NAME}] 第 {index + 1} 条配置缺少指令名，已跳过")
            return
        if " " in command:
            logger.warning(f"[{PLUGIN_NAME}] 指令名「{command}」不能包含空格，已跳过")
            return
        if not reply:
            logger.warning(f"[{PLUGIN_NAME}] 指令「{command}」缺少回复内容，已跳过")
            return
        if command in self._registered:
            logger.warning(f"[{PLUGIN_NAME}] 指令「{command}」重复配置，已跳过后者")
            return

        async def handler(event: AstrMessageEvent):
            # 回复内容在注册时通过闭包绑定，每条指令互不干扰
            yield event.plain_result(reply)
            # 阻断事件继续传播，避免再触发其他插件的监听或 LLM 请求
            event.stop_event()

        # 注册表以「模块名_函数名」作为唯一 key，闭包函数名默认相同，必须改成唯一
        handler.__name__ = f"dynamic_cmd_{index}"
        handler.__doc__ = desc  # 指令描述，会显示在菜单面板插件的指令选择器中

        # 调用官方装饰器完成注册，效果等价于 @filter.command(command)
        filter.command(command, alias=aliases, priority=COMMAND_PRIORITY)(handler)
        self._registered.append(command)

    # ------------------------------------------------------------------
    # 管理指令：在聊天中查看当前生效的自定义指令
    # ------------------------------------------------------------------
    @filter.command("customcmd", alias={"自定义指令"})
    async def list_custom_commands(self, event: AstrMessageEvent):
        """查看本插件当前注册的所有自定义指令"""
        if not self._registered:
            yield event.plain_result(
                "当前没有已注册的自定义指令。\n"
                "请在 WebUI 插件管理中打开本插件配置：点击「修改」新增键值对"
                "（键 = 指令名，值 = 回复内容），保存并重载插件后生效。"
            )
            return
        lines = [f"当前已注册 {len(self._registered)} 条自定义指令："]
        for i, item in enumerate(self._get_command_items(), 1):
            cmd = str(item.get("command", "")).strip().lstrip("/")
            if not cmd:
                continue
            desc = str(item.get("desc", "")).strip()
            lines.append(f"{i}. /{cmd}" + (f" —— {desc}" if desc else ""))
        lines.append("\n修改配置请在 WebUI 中编辑本插件配置，保存后重载插件生效。")
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        """插件卸载 / 停用时的清理（注册的指令会由框架按模块路径自动移除）"""
        self._registered.clear()
        logger.info(f"[{PLUGIN_NAME}] 插件已卸载")
