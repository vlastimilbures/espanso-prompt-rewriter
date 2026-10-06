"""The Try tab (#111): rewrite a draft typed here, the way a trigger would, and read the
result before any trigger pastes one.

The draft is typed into the tab; the clipboard is never read or written. The rewrite runs in
process, in a worker thread, through cli.rewrite() (so the draft never appears in a command
line or the process list), and with it through factory.make_provider() and its
data-protection gate. By default it runs against the set-up smoke test's stub on 127.0.0.1
with a placeholder key: no provider is called and nothing is recorded. A real provider call
first asks, naming the provider and the model, and is then recorded in the usage history as
a direct call (no trigger), as `promptmend improve` typed in a terminal would be. The result
is shown as plain text, never read as markup.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Select, Static, TextArea

from .. import cli, recorder, smoke
from ..commands import common
from ..config import TIERS, Settings, openrouter_only
from ..factory import PROVIDER_NAMES
from ..prompt_builder import ADDED, PROFILES
from . import teach
from .modals import ConfirmModal
from .panes import Pane, _buttons

if TYPE_CHECKING:
    from ..config import ConfigLayers
    from ..providers.usage import AttemptUsage
    from .state import State

STUB = "stub"
REAL = "real"
TARGETS = (("Local stub", STUB), ("Real provider", REAL))
# What the session log shows in place of the draft, which never leaves this tab.
DRAFT_WITHHELD = "<draft withheld>"


class DraftArea(TextArea):
    """The draft. It keeps every key typed into it (Tab moves on); Escape leaves it, so the
    tab keys work again."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "leave", "Leave")]

    def action_leave(self) -> None:
        self.screen.set_focus(None)


@dataclass(frozen=True)
class Outcome:
    """A finished run: what to show, whether it failed, and the usage line."""

    text: str
    error: bool
    usage: str
    requests: int | None


def _tokens(values: Sequence[int | None]) -> str:
    known = [v for v in values if v is not None]
    return str(sum(known)) if known else "unknown"


def _cost(attempts: Sequence[AttemptUsage], *, stub: bool) -> str:
    """Reported, unknown or not applicable: a cost nobody reported is never shown as 0."""
    if stub:
        return "not applicable (local stub)"
    if all(a.cost_state == "not_applicable" for a in attempts):
        return "not applicable (runs on this machine)"
    totals: dict[str, Decimal] = {}
    for usage in attempts:
        if usage.charged_amount is not None and usage.charged_unit is not None:
            totals[usage.charged_unit] = totals.get(usage.charged_unit, Decimal(0)) + (
                usage.charged_amount
            )
    if not totals:
        return "unknown"
    shown = " + ".join(f"{amount} {unit}" for unit, amount in totals.items())
    unreported = any(a.charged_amount is None and a.cost_state == "unknown" for a in attempts)
    return f"{shown} reported{' (some unknown)' if unreported else ''}"


def usage_line(attempts: Sequence[AttemptUsage], latency_ms: float, *, stub: bool) -> str:
    """One line on what a run took: time, requests, tokens in and out, and cost."""
    if not attempts:
        return f"{latency_ms:.0f} ms · no request was sent"
    inputs = [
        None
        if a.input_uncached is None and a.cache_read is None and a.cache_write is None
        else (a.input_uncached or 0) + (a.cache_read or 0) + (a.cache_write or 0)
        for a in attempts
    ]
    requests = f"{len(attempts)} request{'' if len(attempts) == 1 else 's'}"
    return (
        f"{latency_ms:.0f} ms · {requests} · tokens in {_tokens(inputs)}, "
        f"out {_tokens([a.output for a in attempts])} · cost {_cost(attempts, stub=stub)}"
    )


def _model_setting(provider: str, tier: str) -> str:
    """The setting that names the model a call runs (for_call(): pro only on OpenRouter)."""
    if provider == "openrouter" and tier == "pro":
        return "OPENROUTER_PRO_MODEL"
    return f"{provider.upper()}_MODEL"


class TryPane(Pane):
    # A run is under way (one at a time; Run is disabled meanwhile).
    running = False
    # The provider and profile pickers start at the configured ones, once: a reload after a
    # change elsewhere keeps what was picked here.
    defaulted = False
    # The stub's request count of the last stub run (0 when the gate stopped it first).
    stub_requests: int | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            "Type a draft and rewrite it as a trigger would. The clipboard is never read. "
            "Local stub answers on 127.0.0.1 and calls no provider; a real provider asks "
            "first, goes through the data-protection gate and is recorded in the usage "
            "history as a direct call.",
            classes="note",
            markup=False,
        )
        yield DraftArea(
            id="try-draft", placeholder="Your draft (Escape leaves the box)", soft_wrap=True
        )
        with Horizontal(id="try-options"):
            for id_, label, options, value in (
                ("try-target", "Run against", TARGETS, STUB),
                ("try-provider", "Provider", [(n, n) for n in PROVIDER_NAMES], PROVIDER_NAMES[0]),
                ("try-profile", "Profile", [(n, n) for n in PROFILES], next(iter(PROFILES))),
                ("try-tier", "Tier", [(t, t) for t in TIERS], TIERS[0]),
            ):
                with Vertical(classes="try-option"):
                    yield Label(label)
                    yield Select(options, value=value, allow_blank=False, id=id_)
        yield _buttons(("try-run", "Run"))
        yield Static("No run yet.", id="try-usage", markup=False)
        yield TextArea("", id="try-result", read_only=True, soft_wrap=True)
        yield self.result()

    def show(self, state: State) -> None:
        profiles = self.query_one("#try-profile", Select)
        picked = profiles.value
        names = [*PROFILES, *(p.name for p in state.profiles if p.status == ADDED)]
        profiles.set_options([(n, n) for n in names])
        if not self.defaulted:
            self.defaulted = True
            picked = state.settings.profile
            providers = self.query_one("#try-provider", Select)
            if state.settings.provider in PROVIDER_NAMES:
                providers.value = state.settings.provider
        if picked in names:
            profiles.value = picked

    def _value(self, selector: str) -> str:
        return str(self.query_one(selector, Select).value)

    @on(Button.Pressed, "#try-run")
    def _run(self) -> None:
        if self.running:
            return
        draft = self.query_one("#try-draft", TextArea).text
        target = self._value("#try-target")
        provider, profile = self._value("#try-provider"), self._value("#try-profile")
        tier = self._value("#try-tier")
        try:
            layers, loaded = common.load_layers()
            cfg = loaded.for_call(tier, provider=provider)
            openrouter_only(provider, tier, None)
        except Exception as exc:
            self._finish(Outcome(cli._clean(cli.marker(exc)), True, "", None), None)
            return
        if target == STUB:
            self._start(draft, provider, profile, cfg, None)
            return
        command = teach.equivalent(
            "improve",
            "--provider",
            provider,
            "--profile",
            profile,
            "--tier",
            tier,
            "--source",
            "argument",
            "--text",
            DRAFT_WITHHELD,
        )

        def done(yes: bool | None) -> None:
            if yes:
                self._start(draft, provider, profile, cfg, command)

        self.app.push_screen(
            ConfirmModal(
                f"Send this draft to {provider}?",
                self._preview(layers, cfg, provider, tier, len(draft)),
                confirm="Send",
            ),
            done,
        )

    def _preview(
        self, layers: ConfigLayers, cfg: Settings, provider: str, tier: str, length: int
    ) -> str:
        model = _model_setting(provider, tier)
        url = f"{provider.upper()}_BASE_URL"
        recorded = (
            "It is recorded in the usage history as a direct call (no trigger)."
            if cfg.history
            else "PROMPT_HISTORY is off: nothing is recorded."
        )
        return "\n".join(
            [
                f"Provider: {provider}",
                f"Model: {common.shown_value(model, layers.entries[model])}",
                f"Base URL: {common.shown_value(url, layers.entries[url])}",
                f"Draft: {length} characters, typed here (the clipboard is not read)",
                "",
                "It goes through the data-protection gate as a trigger's draft does. A cloud "
                "provider may charge for the call.",
                recorded,
            ]
        )

    def _start(
        self, draft: str, provider: str, profile: str, cfg: Settings, command: str | None
    ) -> None:
        self.running = True
        self.query_one("#try-run", Button).disabled = True
        where = "the local stub" if command is None else provider
        self.query_one("#try-usage", Static).update(f"Rewriting with {where}…")
        self.background(lambda: self._call(draft, provider, profile, cfg, command))

    def _call(
        self, draft: str, provider: str, profile: str, cfg: Settings, command: str | None
    ) -> None:
        """In the worker: one rewrite, through make_provider() and its gate. ``command`` is
        None for a stub run, which records nothing; a real run is recorded as improve."""
        attempts: list[AttemptUsage] = []
        ran = cli.Rewrite()
        rec: recorder.Recorder | None = None
        if command is not None:
            rec = recorder.Recorder("improve")
            rec.track(cfg)
            # Collected whether or not the history is on: the usage line shows them.
            rec.attempts = attempts
        started = time.monotonic()
        requests = 0
        error = False
        try:
            if command is None:
                with smoke.stub_server() as stub:
                    try:
                        text = cli.rewrite(
                            draft,
                            provider,
                            smoke.stub_settings(cfg, stub.port),
                            profile,
                            ran,
                            observer=attempts.append,
                        )
                    finally:
                        requests = stub.requests
            else:
                text = cli.rewrite(draft, provider, cfg, profile, ran, observer=attempts.append)
                requests = len(attempts)
        except Exception as exc:
            error = True
            text = cli.marker(exc)
            if rec is not None:
                rec.outcome = cli._failure(exc, rec)
        latency = (time.monotonic() - started) * 1000
        # Cleaned as improve's output is (_emit()), the sent-despite note included.
        shown = cli._clean(cli._sent_despite_note(ran.built) + text)
        outcome = Outcome(
            shown, error, usage_line(attempts, latency, stub=command is None), requests
        )
        self.app.call_from_thread(self._finish, outcome, command)
        if rec is not None:
            # After the result is on screen, as improve records after printing.
            rec.emitted()
            rec.profile_id = ran.profile_id
            rec.finish()
            # The History tab shows the new call.
            self.app.call_from_thread(self.manage.reload)

    def _finish(self, outcome: Outcome, command: str | None) -> None:
        self.running = False
        self.query_one("#try-run", Button).disabled = False
        self.query_one("#try-result", TextArea).load_text(outcome.text)
        self.query_one("#try-usage", Static).update(outcome.usage)
        if command is None:
            self.stub_requests = outcome.requests
        if outcome.error:
            self.report(outcome.text, error=True, command=command)
        else:
            done = "the local stub answered" if command is None else "rewritten"
            self.report(f"Done: {done}.", command=command)

    def failed(self, exc: Exception) -> None:
        self.running = False
        self.query_one("#try-run", Button).disabled = False
        super().failed(exc)
