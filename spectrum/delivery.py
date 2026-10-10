"""追踪本轮实际成功发送的内容与完整程度。
支持分段、转发、语音原文及已登记的文字渲染结果；发送异常继续向调用方传播。"""

from dataclasses import dataclass, field
from typing import Callable
from types import SimpleNamespace
from . import freeze


_rendered_text: dict[str, str] = {}


def install_render_receipts(renderer, file_service=None) -> None:
    global _rendered_text
    original = renderer.render_t2i
    if getattr(original, "_spectrum_render_receipt", False):
        _rendered_text = original._spectrum_rendered_text
    else:
        async def render(text, *args, **kwargs):
            result = await original(text, *args, **kwargs)
            if isinstance(result, str) and result:
                _rendered_text[result] = text
                if len(_rendered_text) > 256:
                    _rendered_text.pop(next(iter(_rendered_text)))
            return result

        render._spectrum_render_receipt = True
        render._spectrum_rendered_text = _rendered_text
        renderer.render_t2i = render

    if file_service is not None and not getattr(file_service.register_file, "_spectrum_render_receipt", False):
        register_file = file_service.register_file

        async def register(path, *args, **kwargs):
            token = await register_file(path, *args, **kwargs)
            if str(path) in _rendered_text:
                _rendered_text[str(token)] = _rendered_text[str(path)]
                if len(_rendered_text) > 256:
                    _rendered_text.pop(next(iter(_rendered_text)))
            return token

        register._spectrum_render_receipt = True
        file_service.register_file = register


def rendered_content(message, extract_text: Callable) -> str:
    """提取发送组件中的文本来源，不为未登记的任意图片推断文字。"""
    chunks = []
    for component in message.chain or []:
        kind = type(component).__name__
        if kind == "Record":
            chunks.append(str(getattr(component, "text", "") or ""))
        elif kind == "Node":
            node = SimpleNamespace(chain=component.content)
            chunks.append(rendered_content(node, extract_text))
        elif kind == "Nodes":
            node = SimpleNamespace(chain=component.nodes)
            chunks.append(rendered_content(node, extract_text))
        elif kind == "Image":
            file = str(getattr(component, "file", "") or "")
            url = str(getattr(component, "url", "") or "")
            resource = file or url
            token = resource.rsplit("/api/file/", 1)[-1] if "/api/file/" in resource else ""
            chunks.append(_rendered_text.get(file, _rendered_text.get(url, _rendered_text.get(token, ""))))
        else:
            node = SimpleNamespace(chain=[component])
            chunks.append(extract_text(node))
    return "".join(chunks)


@dataclass
class ReplyDelivery:
    decision: object
    receipts: dict
    chunks: list[str] = field(default_factory=list)
    failed: bool = False
    recorded: bool = False

    @property
    def text(self) -> str:
        return "".join(self.chunks)

    @property
    def complete(self) -> bool:
        """忽略分段分隔符后核对内容覆盖；任一发送失败仍判为不完整。"""
        actual = "".join(c for c in self.text if not c.isspace() and c not in "。？！~…")
        expected = "".join(c for c in self.decision.content if not c.isspace() and c not in "。？！~…")
        return bool(expected) and expected in actual and not self.failed


def track_reply(event, decision, receipts: dict, extract_text: Callable) -> ReplyDelivery:
    """包装事件发送，只有实际调用成功后才累计可识别的已发送文字。"""
    delivery = ReplyDelivery(decision, dict(receipts))
    original_send = event.send
    epoch = event.get_extra("_spectrum_freeze_epoch", freeze.checkpoint())

    async def send_with_receipt(message):
        try:
            if not freeze.may_commit(epoch):
                raise freeze.FrozenSkipped("回复所属的运行阶段已结束")
            result = await original_send(message)
        except Exception as error:
            delivery.failed = True
            freeze.record_failure("delivery", error)
            raise
        text = rendered_content(message, extract_text)
        if text:
            delivery.chunks.append(text)
        return result

    event.send = send_with_receipt
    event.set_extra("_spectrum_reply_delivery", delivery)
    return delivery
