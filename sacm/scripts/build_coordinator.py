"""
build_coordinator.py — Multi-agent iOS build coordinator z pętlą uczenia.

Pętla sprzężenia zwrotnego (feedback loop)
------------------------------------------
Każde uruchomienie jest *epizodem*. Po zakończeniu:

1. Kształtowana nagroda (shaped reward):
     tsc_ok        → +0.20
     eslint_ok     → +0.10
     build_success → +0.50
     submit_ok     → +0.20
     speed_bonus   → do +0.10 (gdy szybsze niż EMA baseline)

2. Advantage = reward − EMA_baseline  (redukuje wariancję — REINFORCE)

3. FSM EMA: każda tranzycja aktualizuje accuracy:
     accuracy ← α * (reward × confidence) + (1−α) * accuracy

4. PyTorch router (RouterService): gradient REINFORCE z advantage
   → wagi zapisywane atomowo do sacm_build_router.pt

5. Historia uruchomień → sacm_build_history.json (warm-start przy
   kolejnym uruchomieniu: ładuje EMA baseline + poprzednie accuracy FSM)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sacm.agents.cloud_executor import CloudExecutorAgent
from sacm.agents.context_agent import ContextAgent
from sacm.agents.frontend_agent import FrontendAgent
from sacm.agents.infrastructure_agent import InfrastructureAgent
from sacm.agents.reviewer import ReviewerAgent
from sacm.core.router import RouterService
from sacm.core.state_machine import AgentFSM
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult

# ─── Stałe uczenia ────────────────────────────────────────────────────────────
FSM_ALPHA        = float(os.getenv("SACM_BUILD_FSM_ALPHA",        "0.12"))
BASELINE_ALPHA   = float(os.getenv("SACM_BUILD_BASELINE_ALPHA",   "0.10"))
CHECKPOINT_EVERY = int(os.getenv("SACM_BUILD_CHECKPOINT_EVERY",   "3"))

# Wagi nagród za poszczególne fazy
REWARD_WEIGHTS = {
    "tsc_ok":        0.20,
    "eslint_ok":     0.10,
    "build_success": 0.50,
    "submit_ok":     0.20,
    "speed_bonus":   0.10,
}

# ─── Kolory ───────────────────────────────────────────────────────────────────
G = "\033[32m"
Y = "\033[33m"
C = "\033[36m"
B = "\033[1m"
R = "\033[0m"

def log(agent: str, msg: str, color: str = C) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{color}{B}[{ts}] [{agent}]{R} {msg}")


# ─── Historia uruchomień ──────────────────────────────────────────────────────
@dataclass
class BuildRunRecord:
    run_id:        str
    timestamp:     str
    tsc_ok:        bool
    eslint_ok:     bool
    build_success: bool
    submit_ok:     bool
    elapsed_s:     float
    reward:        float
    advantage:     float
    skill_rewards: dict[str, float] = field(default_factory=dict)
    ipa_path:      str | None       = None


@dataclass
class BuildHistory:
    """Persystencja historii build runów + EMA baseline."""
    path:             str
    ema_baseline:     float = 0.5
    ema_elapsed_s:    float = 900.0          # zakładamy ~15 min jako pierwsze przybliżenie
    total_runs:       int   = 0
    successful_runs:  int   = 0
    records:          list[BuildRunRecord] = field(default_factory=list)

    # ── Persystencja ─────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str) -> "BuildHistory":
        if os.path.exists(path):
            try:
                with open(path) as fh:
                    d = json.load(fh)
                records = [BuildRunRecord(**r) for r in d.get("records", [])]
                return cls(
                    path=path,
                    ema_baseline=d.get("ema_baseline", 0.5),
                    ema_elapsed_s=d.get("ema_elapsed_s", 900.0),
                    total_runs=d.get("total_runs", 0),
                    successful_runs=d.get("successful_runs", 0),
                    records=records,
                )
            except Exception:
                pass
        return cls(path=path)

    def save(self) -> None:
        tmp = self.path + ".tmp"
        data = {
            "ema_baseline":    self.ema_baseline,
            "ema_elapsed_s":   self.ema_elapsed_s,
            "total_runs":      self.total_runs,
            "successful_runs": self.successful_runs,
            "records":         [asdict(r) for r in self.records[-50:]],  # max 50 rekordów
        }
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, self.path)

    # ── Obliczenia nagrody ────────────────────────────────────────────────────
    def compute_reward(
        self, tsc_ok: bool, eslint_ok: bool,
        build_ok: bool, submit_ok: bool, elapsed_s: float,
    ) -> float:
        r = 0.0
        if tsc_ok:
            r += REWARD_WEIGHTS["tsc_ok"]
        if eslint_ok:
            r += REWARD_WEIGHTS["eslint_ok"]
        if build_ok:
            r += REWARD_WEIGHTS["build_success"]
        if submit_ok:
            r += REWARD_WEIGHTS["submit_ok"]
        # Speed bonus: każda sekunda poniżej baseline przynosi proporcjonalną nagrodę
        if elapsed_s < self.ema_elapsed_s and self.ema_elapsed_s > 0:
            speed_ratio = 1.0 - elapsed_s / self.ema_elapsed_s
            r += REWARD_WEIGHTS["speed_bonus"] * min(speed_ratio, 1.0)
        return round(min(r, 1.0), 4)

    def compute_advantage(self, reward: float) -> float:
        """Advantage = reward − EMA_baseline  (redukuje wariancję gradientu)."""
        return round(reward - self.ema_baseline, 4)

    def record(
        self,
        tsc_ok: bool, eslint_ok: bool,
        build_ok: bool, submit_ok: bool,
        elapsed_s: float, skill_rewards: dict[str, float],
        ipa_path: str | None,
    ) -> BuildRunRecord:
        reward    = self.compute_reward(tsc_ok, eslint_ok, build_ok, submit_ok, elapsed_s)
        advantage = self.compute_advantage(reward)

        # Aktualizuj EMA
        self.ema_baseline  = BASELINE_ALPHA * reward    + (1 - BASELINE_ALPHA) * self.ema_baseline
        self.ema_elapsed_s = BASELINE_ALPHA * elapsed_s + (1 - BASELINE_ALPHA) * self.ema_elapsed_s
        self.total_runs    += 1
        if build_ok:
            self.successful_runs += 1

        run = BuildRunRecord(
            run_id        = f"run-{self.total_runs:04d}",
            timestamp     = datetime.now().isoformat(),
            tsc_ok        = tsc_ok,
            eslint_ok     = eslint_ok,
            build_success = build_ok,
            submit_ok     = submit_ok,
            elapsed_s     = round(elapsed_s, 1),
            reward        = reward,
            advantage     = advantage,
            skill_rewards = skill_rewards,
            ipa_path      = ipa_path,
        )
        self.records.append(run)
        self.save()
        return run

    def summary_str(self) -> str:
        if self.total_runs == 0:
            return "brak historii (pierwsze uruchomienie)"
        rate = self.successful_runs / self.total_runs * 100
        return (
            f"runs={self.total_runs}  "
            f"success_rate={rate:.0f}%  "
            f"ema_baseline={self.ema_baseline:.3f}  "
            f"ema_elapsed={self.ema_elapsed_s/60:.1f}min"
        )


# ─── REINFORCE update FSM + Router ────────────────────────────────────────────
def apply_feedback(
    fsm:          AgentFSM,
    router:       RouterService,
    history:      BuildHistory,
    run:          BuildRunRecord,
    skill_ledger: dict[str, Any],
) -> None:
    """Aktualizuje wagi FSM i PyTorch routera na podstawie wyniku epizodu."""
    log("FeedbackLoop",
        f"reward={run.reward:.3f}  advantage={run.advantage:+.3f}  "
        f"({'improvement' if run.advantage > 0 else 'below baseline'})",
        G if run.advantage >= 0 else Y)

    # 1. FSM: zaktualizuj accuracy każdego skilla który był użyty
    for skill_name, skill_conf in run.skill_rewards.items():
        # Kształtowana nagroda uwzględnia pewność agenta + końcową nagrodę epizodu
        shaped = skill_conf * run.reward
        fsm.update(skill_name, reward=shaped)
        if __debug__:
            t = next((t for t in fsm.transitions if t.skill_name == skill_name), None)
            if t:
                log("FSM", f"  {skill_name}: accuracy → {t.accuracy:.4f}", C)

    # 2. PyTorch router: REINFORCE z advantage
    # Budujemy uproszczony wektor kontekstu z cech epizodu
    ctx_vec = torch.tensor([
        float(run.tsc_ok),
        float(run.eslint_ok),
        float(run.build_success),
        run.elapsed_s / 1800.0,          # normalizacja: 30min = 1.0
        len(run.skill_rewards) / 10.0,   # ile skillli zdobyto
        run.reward,
        math.tanh(run.advantage),        # bounded advantage
        float(history.total_runs) / 100, # doświadczenie
    ], dtype=torch.float32)

    # Pad do CONTEXT_DIM (768) zerami
    from sacm.core.router import CONTEXT_DIM
    if ctx_vec.shape[0] < CONTEXT_DIM:
        ctx_vec = torch.cat([ctx_vec, torch.zeros(CONTEXT_DIM - ctx_vec.shape[0])])

    # Wybierz agenta który wniósł największy skill (jako proxy "wybranego agenta")
    AGENT_MAP = {
        "infra_planned": 8, "deployment_configured": 8,
        "ui_designed": 7, "components_planned": 7,
        "orchestration_complete": 10, "work_distributed": 10,
        "code_implemented": 1, "tests_run": 9,
        "code_reviewed": 2,
    }
    best_agent_idx = max(
        (AGENT_MAP.get(sk, 0) for sk in run.skill_rewards),
        default=0,
    )

    # Wektor belief (równomierny jako prior)
    belief = torch.ones(7) / 7.0

    router.update(
        context_vector=ctx_vec.tolist(),
        belief_state=belief.tolist(),
        selected_agent_index=best_agent_idx,
        advantage=run.advantage,
    )

    # 3. Checkpoint routera co CHECKPOINT_EVERY uruchomień
    if history.total_runs % CHECKPOINT_EVERY == 0:
        router.save_weights()
        log("FeedbackLoop", f"Checkpoint routera zapisany (run #{history.total_runs})", G)


# ─── Helpery ──────────────────────────────────────────────────────────────────
def run_agent(name: str, agent, ctx: AgentContext) -> AgentResult:
    log(name, f"start → state={ctx.current_state}")
    t0 = time.time()
    result = agent.run(ctx)
    elapsed = time.time() - t0
    skills = [s["skill_name"] for s in result.skills_contributed]
    log(name, f"✓ {elapsed:.1f}s  conf={result.confidence:.2f}  skills={skills}", G)
    if result.summary:
        print(f"       └─ {result.summary[:110]}")
    return result


def wait_for_build(pid: int | None, project_dir: str, timeout: int = 3600) -> str | None:
    if pid:
        log("Monitor", f"Oczekuję na PID={pid}…", Y)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(5)
            except ProcessLookupError:
                break
        else:
            log("Monitor", "⚠ Timeout", Y)
            return None
        log("Monitor", f"PID {pid} zakończony", G)

    p = Path(project_dir)
    candidates = sorted(
        list(p.glob("*.ipa")) + list((p / "ios" / "build").glob("**/*.ipa")),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    if candidates:
        ipa = str(candidates[0])
        log("Monitor", f"IPA: {ipa}", G)
        return ipa
    log("Monitor", "Nie znaleziono .ipa", Y)
    return None


# ─── Phase 1: równoległe sprawdzenia ─────────────────────────────────────────
def phase1_parallel(project_dir: str, base_ctx: AgentContext) -> dict[str, Any]:
    results: dict[str, Any] = {}

    def tsc():
        r = subprocess.run(
            ["npx", "tsc", "--noEmit", "--skipLibCheck"],
            cwd=project_dir, capture_output=True, text=True, timeout=120,
        )
        return "tsc", r.returncode == 0, (r.stdout + r.stderr)

    def eslint():
        r = subprocess.run(
            ["npx", "eslint", "src/", "--ext", ".ts,.tsx",
             "--max-warnings=20", "--format=compact", "--quiet"],
            cwd=project_dir, capture_output=True, text=True, timeout=120,
        )
        return "eslint", r.returncode == 0, (r.stdout + r.stderr)

    def infra():
        ctx = base_ctx.model_copy(update={"current_state": "planning"})
        return "infra", run_agent("InfrastructureAgent", InfrastructureAgent(), ctx)

    def frontend():
        ctx = base_ctx.model_copy(update={"current_state": "coding"})
        return "frontend", run_agent("FrontendAgent", FrontendAgent(), ctx)

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="p1") as ex:
        futures = {ex.submit(fn): fn.__name__ for fn in [tsc, eslint, infra, frontend]}
        for fut in as_completed(futures):
            try:
                val = fut.result()
                results[val[0]] = val
            except Exception as exc:
                log("Phase1", f"⚠ błąd: {exc}", Y)
    return results


def phase2_submit(ipa_path: str | None, project_dir: str, ctx: AgentContext) -> bool:
    rev_ctx = ctx.model_copy(update={
        "current_state": "reviewing",
        "previous_findings": ctx.previous_findings + [
            f"IPA: {ipa_path or 'latest EAS build'}"
        ],
    })
    run_agent("ReviewerAgent", ReviewerAgent(), rev_ctx)

    cmd = (
        f"cd {project_dir} && eas submit --platform ios --path {ipa_path} --non-interactive"
        if ipa_path else
        f"cd {project_dir} && eas submit --platform ios --latest --non-interactive"
    )
    exec_ctx = ctx.model_copy(update={
        "current_state": "testing",
        "test_command": cmd,
        "target_repo_path": project_dir,
    })
    result = run_agent("CloudExecutorAgent", CloudExecutorAgent(), exec_ctx)
    ok = result.next_state_hint not in ("blocked", "debugging")
    if ok:
        log("Coordinator", "✅ Submit do TestFlight zakończony!", G)
    else:
        log("Coordinator", f"⚠ Submit nie powiódł się: {result.summary[:80]}", Y)
    return ok


# ─── Główna pętla ─────────────────────────────────────────────────────────────
def run_coordinator(
    project_dir: str,
    build_pid:   int | None,
    auto_submit: bool,
    fsm_path:    str | None = None,
    ipa_path:    str | None = None,
) -> None:
    project_dir = str(Path(project_dir).resolve())
    hist_path   = os.path.join(project_dir, "sacm_build_history.json")
    fsm_path_   = fsm_path or os.path.join(project_dir, "sacm_build_fsm.json")
    router_path = os.path.join(project_dir, "sacm_build_router.pt")

    # ── Wczytaj historię i załaduj wcześniejsze wagi ──────────────────────
    history = BuildHistory.load(hist_path)
    fsm     = AgentFSM(path=fsm_path_)
    router  = RouterService()
    if os.path.exists(router_path):
        router._load_weights_safe(router_path)
        log("FeedbackLoop", f"Załadowano wagi routera z {router_path}", G)

    print(f"\n{B}{'─'*62}{R}")
    print(f"{B} sacm-agent-runtime  ·  iOS Build Coordinator + Feedback Loop{R}")
    print(f"{B}{'─'*62}{R}")
    print(f"  Projekt:    {project_dir}")
    print(f"  Build PID:  {build_pid or ('skip (IPA podane)' if ipa_path else 'auto-detect')}")
    print(f"  IPA:        {ipa_path or 'czekam na build'}")
    print(f"  Submit:     {'tak' if auto_submit else 'nie'}")
    print(f"  Historia:   {history.summary_str()}")
    print()

    base_ctx = AgentContext(
        task_id="ios-build-coordinator",
        task="Build iOS app, run quality checks, submit to TestFlight",
        goal="Produce production .ipa, validate quality, push to TestFlight",
        current_state="planning",
        target_repo_path=project_dir,
        constraints=["Read-only checks only", "Do not modify source files"],
        relevant_memory=[
            "Build number auto-incremented to 295",
            "4 bugs fixed: PDF, tablet layout, Polygon ERC-20, ML materials",
            "TS: 0 errors  |  Dist cert valid 2027-03-27",
            f"Poprzednie buildy: {history.summary_str()}",
        ],
    )

    t0           = time.time()
    skill_ledger: dict[str, Any] = {}
    skill_confs:  dict[str, float] = {}   # skill_name → confidence (do nagrody)

    # ── Phase 1: równolegle z Xcode ───────────────────────────────────────
    log("Coordinator", f"{B}Phase 1 — Równoległe sprawdzenia (podczas buildu Xcode){R}")
    p1 = phase1_parallel(project_dir, base_ctx)

    for _key, val in p1.items():
        if isinstance(val, tuple) and len(val) == 2 and isinstance(val[1], AgentResult):
            r: AgentResult = val[1]
            for s in r.skills_contributed:
                skill_ledger[s["skill_name"]] = s
                skill_confs[s["skill_name"]]  = float(s.get("confidence", r.confidence))

    tsc_ok    = p1.get("tsc",    ("", False, ""))[1]
    eslint_ok = p1.get("eslint", ("", False, ""))[1]
    log("Quality", f"tsc: {'✅' if tsc_ok else '⚠'} | eslint: {'✅' if eslint_ok else '⚠'}",
        G if (tsc_ok and eslint_ok) else Y)

    # ContextAgent agreguje Phase 1
    enriched = base_ctx.model_copy(update={
        "skill_state": skill_ledger,
        "previous_findings": [
            f"tsc: {'OK' if tsc_ok else 'ERRORS'}",
            f"eslint: {'OK' if eslint_ok else 'WARNINGS'}",
            f"ema_baseline: {history.ema_baseline:.3f}",
            f"ema_elapsed:  {history.ema_elapsed_s/60:.1f} min",
            f"proven_skills: {list(skill_ledger.keys())}",
        ],
    })
    log("Coordinator", "ContextAgent — agreguje skills z Phase 1")
    ctx_r = run_agent("ContextAgent", ContextAgent(), enriched)
    for s in ctx_r.skills_contributed:
        skill_ledger[s["skill_name"]] = s
        skill_confs[s["skill_name"]]  = float(s.get("confidence", ctx_r.confidence))

    # ── Phase 2: czekaj na build (lub użyj podanego IPA) ─────────────────
    log("Coordinator", f"{B}Phase 2 — Oczekiwanie na EAS build…{R}", Y)
    ipa: str | None
    if ipa_path and Path(ipa_path).exists():
        log("Monitor", f"IPA podane bezpośrednio: {ipa_path}", G)
        ipa = ipa_path
    else:
        ipa = wait_for_build(build_pid, project_dir)
    build_ok = ipa is not None

    submit_ok = False
    if auto_submit:
        log("Coordinator", f"{B}Phase 2 — Submit do TestFlight{R}")
        submit_ok = phase2_submit(ipa, project_dir, enriched)

    elapsed = time.time() - t0

    # ── Pętla sprzężenia zwrotnego ────────────────────────────────────────
    log("FeedbackLoop", "Obliczam nagrodę i aktualizuję wagi FSM + router…")
    run_record = history.record(
        tsc_ok=tsc_ok, eslint_ok=eslint_ok,
        build_ok=build_ok, submit_ok=submit_ok,
        elapsed_s=elapsed, skill_rewards=skill_confs, ipa_path=ipa,
    )
    apply_feedback(fsm, router, history, run_record, skill_ledger)

    # ── Podsumowanie ──────────────────────────────────────────────────────
    best = fsm.best_transition("planning", set(skill_ledger.keys()))
    success_rate = history.successful_runs / max(history.total_runs, 1) * 100

    print(f"\n{B}{'─'*62}{R}")
    print(f"{B} Pipeline zakończony  ({elapsed:.1f}s){R}")
    print(f"{'─'*62}")
    print(f"  TS check:       {'✅' if tsc_ok else '⚠'}")
    print(f"  ESLint:         {'✅' if eslint_ok else '⚠'}")
    print(f"  Build:          {'✅' if build_ok else '⚠'}")
    print(f"  Submit:         {'✅' if submit_ok else '—'}")
    print(f"  Nagroda:        {run_record.reward:.3f}  (advantage {run_record.advantage:+.3f})")
    print(f"  EMA baseline:   {history.ema_baseline:.3f}  →  target dla kolejnego buildu")
    print(f"  Buildy łącznie: {history.total_runs}  (success {success_rate:.0f}%)")
    print(f"  Śr. czas buildu:{history.ema_elapsed_s/60:.1f} min")
    if best:
        print(f"  FSM next best:  {best.skill_name}  (accuracy={best.accuracy:.4f})")
    print(f"{'─'*62}\n")

    # Raport JSON
    report = {
        "run": asdict(run_record),
        "history_summary": {
            "total_runs": history.total_runs,
            "success_rate_pct": round(success_rate, 1),
            "ema_baseline": round(history.ema_baseline, 4),
            "ema_elapsed_min": round(history.ema_elapsed_s / 60, 2),
        },
        "fsm_top_transitions": [
            {"skill": t.skill_name, "from": t.from_state, "accuracy": round(t.accuracy, 4)}
            for t in sorted(fsm.transitions, key=lambda x: -x.accuracy)[:5]
        ],
    }
    rp = Path(project_dir) / "build-coordinator-report.json"
    rp.write_text(json.dumps(report, indent=2, default=str))
    log("Coordinator", f"Raport → {rp}")


def main() -> None:
    p = argparse.ArgumentParser(description="sacm iOS Build Coordinator")
    p.add_argument("--project",   required=True)
    p.add_argument("--build-pid", type=int, default=None)
    p.add_argument("--submit",    action="store_true", default=False)
    p.add_argument("--fsm-path",  default=None)
    p.add_argument("--ipa",       default=None, help="Gotowy plik .ipa — pomija czekanie na build")
    args = p.parse_args()
    run_coordinator(args.project, args.build_pid, args.submit, args.fsm_path, args.ipa)


if __name__ == "__main__":
    main()
