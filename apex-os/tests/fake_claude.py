"""Scripted stand-in for anthropic.Anthropic: returns queued responses, records every request."""
from __future__ import annotations

import itertools
from types import SimpleNamespace

_ids = itertools.count(1)


class Blk(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        out = {}
        for k, v in vars(self).items():
            if v is None and exclude_none:
                continue
            if isinstance(v, list):
                v = [x.model_dump() if hasattr(x, "model_dump") else x for x in v]
            elif hasattr(v, "model_dump"):
                v = v.model_dump()
            out[k] = v
        return out


def text(t):
    return Blk(type="text", text=t)


def tool(name, **inp):
    return Blk(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=inp)


def search_result(*urls):
    return [Blk(type="server_tool_use", id="srv_1", name="web_search", input={"query": "q"}),
            Blk(type="web_search_tool_result", tool_use_id="srv_1",
                content=[Blk(type="web_search_result", url=u, title=f"T {u}") for u in urls])]


def resp(*blocks, stop="tool_use", inp=100, out=50):
    flat = []
    for b in blocks:
        flat.extend(b if isinstance(b, list) else [b])
    return SimpleNamespace(content=flat, stop_reason=stop, model="claude-opus-5-5", stop_details=None,
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=0))


class FakeClaude:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        import copy
        self.calls.append(copy.deepcopy(kw))
        if not self.queue:
            return resp(text("done"), stop="end_turn")
        r = self.queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
