"""``./hunterx improve ...`` CLI commands for the Autonomous Improvement Kernel.

Subcommands:
- status: Show current champion, candidate harnesses, open defect clusters, and promotion records.
- run: Run a complete closed-loop self-improvement cycle (evaluate -> cluster -> synthesize -> benchmark -> promote).
- evaluate: Grade recent un-evaluated application traces with the 3-tier evaluation engine.
- cluster: Cluster failure traces into defect taxonomies and update replay test cases.
- synthesize: Synthesize challenger harnesses to fix open defect clusters.
- benchmark: Run Champion vs. Challenger tournament on frozen golden and replay benchmark cases.
- rollback: Revert champion to previous active version.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from improvement_kernel import (
    ContinuousImprovementLoop,
    get_current_champion,
    init_kernel_db,
)
from improvement_kernel.storage.models import (
    AgentVersion,
    DefectCluster,
    DefectStatus,
    KernelRun,
    PromotionRecord,
    VersionStatus,
)


@contextmanager
def _session() -> Iterator[Session]:
    init_db()
    init_kernel_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _cmd_improve_status(args: argparse.Namespace) -> int:
    """Display the improvement kernel's current status and tournament history."""
    project = getattr(args, "project", "hunterxjob")
    with _session() as db:
        champion = get_current_champion(db, project=project)

        # Candidates
        candidates = list(
            db.execute(
                select(AgentVersion)
                .where(AgentVersion.project == project, AgentVersion.status == VersionStatus.candidate.value)
                .order_by(AgentVersion.created_at.desc())
            ).scalars().all()
        )

        # Open Defect Clusters
        clusters = list(
            db.execute(
                select(DefectCluster)
                .where(DefectCluster.project == project, DefectCluster.status == DefectStatus.open.value)
                .order_by(DefectCluster.occurrences_count.desc())
            ).scalars().all()
        )

        # Promotion Records
        promotions = list(
            db.execute(
                select(PromotionRecord)
                .where(PromotionRecord.project == project)
                .order_by(PromotionRecord.promoted_at.desc())
                .limit(5)
            ).scalars().all()
        )

        # Runs count
        total_runs = db.query(KernelRun).filter_by(project=project).count()

        print("\n" + "=" * 70)
        print(f"        AUTONOMOUS SELF-IMPROVEMENT KERNEL ({project.upper()})")
        print("=" * 70)
        print(f"Active Champion:   {champion.version} ({champion.git_commit[:8] if champion.git_commit else 'unknown'})")
        print(f"Config Hash:       {champion.config_hash} | Prompt Hash: {champion.prompt_hash}")
        print(f"Total Traced Runs: {total_runs}")
        print("-" * 70)
        print(f"Pending Candidates ({len(candidates)}):")
        for c in candidates:
            print(f"  • {c.version} (created {c.created_at})")
            if c.notes:
                print(f"    Notes: {c.notes}")

        print("-" * 70)
        print(f"Open Defect Clusters ({len(clusters)}):")
        for cl in clusters:
            print(f"  • [{cl.failure_category}/{cl.failure_code}] {cl.pattern_signature} ({cl.occurrences_count} occurrences)")
            if cl.proposed_fix_summary:
                print(f"    Fix: {cl.proposed_fix_summary[:100]}...")

        print("-" * 70)
        print(f"Recent Promotion Decisions ({len(promotions)}):")
        for p in promotions:
            mark = "✓ PROMOTED" if p.decision == "promote_auto" else f"✗ {p.decision.upper()}"
            print(f"  • [{mark}] {p.challenger_version} vs {p.champion_version}")
            print(f"    mean_delta: {p.mean_delta:+.4f} | p-value: {p.p_value:.4f} | Cohen's d: {p.cohens_d:.2f} | regressions: {p.regressions_count}")
            print(f"    Rationale: {p.rationale}")

        print("=" * 70 + "\n")
    return 0


def _cmd_improve_run(args: argparse.Namespace) -> int:
    """Run an end-to-end self-improvement cycle, optionally repeating continuously."""
    import time

    project = getattr(args, "project", "hunterxjob")
    interval = getattr(args, "interval", 0)

    while True:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n[{timestamp}] Initiating autonomous self-improvement cycle for {project}...", flush=True)
        try:
            with _session() as db:
                loop = ContinuousImprovementLoop(session=db, project=project)
                report = loop.run_cycle()

                print("\n" + "=" * 68, flush=True)
                print(f"        AUTONOMOUS SELF-IMPROVEMENT CYCLE ({project.upper()})", flush=True)
                print("=" * 68, flush=True)
                print(f"Active Champion:         {report.champion_version}", flush=True)
                print(f"Golden Benchmark Score:  {report.champion_golden_score * 100:.1f}%", flush=True)
                inv_str = "✓ ALL INVARIANTS PASSED" if report.champion_invariants_passed else "✗ INVARIANT VIOLATIONS"
                print(f"Tier 1 Deterministic:    {inv_str}", flush=True)
                print(f"Traces Evaluated:        {report.evaluated_runs}", flush=True)
                print(f"Active Defect Clusters:  {report.new_clusters_count}", flush=True)
                print(f"Synthesized Challengers: {len(report.synthesized_candidates)}", flush=True)
                for sc in report.synthesized_candidates:
                    print(f"  • {sc}", flush=True)
                if report.benchmark_decisions:
                    print("-" * 68, flush=True)
                    print(f"Benchmark Decisions ({len(report.benchmark_decisions)}):", flush=True)
                    for dec in report.benchmark_decisions:
                        print(f"  • Candidate: {dec['candidate']}", flush=True)
                        print(f"    Decision: {dec['decision']} | delta: {dec['mean_delta']:+.4f} | p-value: {dec['p_value']:.4f} | regressions: {dec['regressions_count']}", flush=True)
                        print(f"    Rationale: {dec['rationale']}", flush=True)
                print("-" * 68, flush=True)
                if report.champion_promoted:
                    print(f"★ NEW CHAMPION PROMOTED: {report.champion_promoted}", flush=True)
                else:
                    print(f"Champion {report.champion_version} retained (benchmark leader with zero regressions).", flush=True)
                print("=" * 68 + "\n", flush=True)
        except KeyboardInterrupt:
            print("\nAutonomous self-improvement loop stopped by user.", flush=True)
            break
        except (RuntimeError, ValueError, OSError, KeyError, AttributeError) as e:
            print(f"Error during self-improvement cycle: {e}", flush=True)

        if interval <= 0:
            break

        print(f"Cycle complete. Sleeping {interval} minute(s) before next cycle (Ctrl+C to stop)...", flush=True)
        try:
            time.sleep(interval * 60)
        except KeyboardInterrupt:
            print("\nAutonomous self-improvement loop stopped by user.")
            break

    return 0


def _cmd_improve_evaluate(args: argparse.Namespace) -> int:
    """Evaluate recent un-graded traces."""
    project = getattr(args, "project", "hunterxjob")
    with _session() as db:
        loop = ContinuousImprovementLoop(session=db, project=project)
        count = loop.evaluate_pending_runs(limit=args.limit)
        print(f"Successfully evaluated {count} execution traces for {project} with 3-tier grading engine.")
    return 0


def _cmd_improve_cluster(args: argparse.Namespace) -> int:
    """Cluster failures into defect taxonomies and update replay test cases."""
    project = getattr(args, "project", "hunterxjob")
    with _session() as db:
        loop = ContinuousImprovementLoop(session=db, project=project)
        clusters_count = loop.cluster_defects()
        print(f"Clustering complete for {project}. Identified {clusters_count} active defect clusters.")
    return 0


def _cmd_improve_synthesize(args: argparse.Namespace) -> int:
    """Synthesize candidate harnesses from open defect clusters."""
    project = getattr(args, "project", "hunterxjob")
    with _session() as db:
        loop = ContinuousImprovementLoop(session=db, project=project)
        candidates = loop.synthesize_candidates()
        print(f"Synthesized {len(candidates)} challenger agent harnesses for {project}:")
        for cand in candidates:
            print(f"  • {cand.version}")
    return 0


def _cmd_improve_benchmark(args: argparse.Namespace) -> int:
    """Run Champion vs. Challenger tournament on frozen golden and replay benchmarks."""
    project = getattr(args, "project", "hunterxjob")
    with _session() as db:
        loop = ContinuousImprovementLoop(session=db, project=project)
        champion = get_current_champion(db, project=project)
        champ_res = loop.sandbox.run_benchmark(champion, loop.golden_path)

        print("\n" + "=" * 68)
        print(f"      BENCHMARK REPORT: CHAMPION {champion.version} ({project})")
        print("=" * 68)
        print(f"Total Test Cases:        {champ_res.runs_count}")
        print(f"Mean Domain Score:       {champ_res.mean_score * 100:.1f}%")
        inv_str = "✓ ALL PASSED" if champ_res.invariants_passed else "✗ VIOLATIONS DETECTED"
        print(f"Tier 1 Invariants:       {champ_res.tier1_pass_rate * 100:.1f}% ({inv_str})")
        print(f"Tier 2 Metrics Pass:     {champ_res.tier2_pass_rate * 100:.1f}%")
        print("-" * 68)

        decisions = loop.benchmark_and_promote()
        if not decisions:
            print("No pending challenger candidates awaiting tournament. Champion is top-ranked.")
        else:
            print(f"Tournament Results ({len(decisions)} candidates evaluated):")
            for dec in decisions:
                print(f"  • {dec['candidate']}: {dec['decision']} (mean_delta: {dec['mean_delta']:+.4f}, regressions: {dec['regressions_count']})")
        print("=" * 68 + "\n")
    return 0


def _cmd_improve_rollback(args: argparse.Namespace) -> int:
    """Roll back champion to previous champion or specified version."""
    project = getattr(args, "project", "hunterxjob")
    with _session() as db:
        champion = get_current_champion(db, project=project)
        target_version = args.version

        if target_version:
            target = db.execute(
                select(AgentVersion).where(
                    AgentVersion.project == project,
                    AgentVersion.version == target_version
                )
            ).scalar_one_or_none()
            if not target:
                print(f"Error: Specified version '{target_version}' not found for {project}.", file=sys.stderr)
                return 1
        else:
            # Pick most recent non-rejected/non-candidate version
            stmt = (
                select(AgentVersion)
                .where(
                    AgentVersion.project == project,
                    AgentVersion.id != champion.id,
                    AgentVersion.status.in_([VersionStatus.canary.value, VersionStatus.staging.value]),
                )
                .order_by(AgentVersion.created_at.desc())
            )
            target = db.execute(stmt).scalars().first()
            if not target:
                print(f"Error: No prior version found to roll back to for {project}.", file=sys.stderr)
                return 1

        print(f"Rolling back active champion from {champion.version} -> {target.version}...")
        champion.status = VersionStatus.rolled_back.value
        target.status = VersionStatus.champion.value

        rollback_record = PromotionRecord(
            project=project,
            champion_version=champion.version,
            challenger_version=target.version,
            decision="rolled_back",
            mean_delta=0.0,
            p_value=1.0,
            cohens_d=0.0,
            regressions_count=0,
            invariants_passed=True,
            rationale=f"Manual administrative rollback from {champion.version} to {target.version}",
        )
        db.add(rollback_record)
        db.commit()
        print(f"Successfully rolled back. Active champion is now: {target.version}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the improve subcommands with the main argument parser."""
    improve_parser = subparsers.add_parser(
        "improve",
        help="autonomous agent self-improvement loop commands (eval, cluster, synthesize, benchmark, promote)",
    )
    sub = improve_parser.add_subparsers(dest="improve_cmd", required=True)

    # status
    p_status = sub.add_parser("status", help="show champion, candidate harnesses, defects, and promotions")
    p_status.set_defaults(handler=_cmd_improve_status)

    # run
    p_run = sub.add_parser("run", help="run end-to-end self-improvement cycle")
    p_run.add_argument(
        "--interval",
        type=int,
        default=0,
        help="repeat cycle every N minutes continuously throughout the day (0 = single run)",
    )
    p_run.set_defaults(handler=_cmd_improve_run)

    # evaluate
    p_eval = sub.add_parser("evaluate", help="evaluate recent un-graded traces")
    p_eval.add_argument("--limit", type=int, default=50, help="max runs to evaluate (default: 50)")
    p_eval.set_defaults(handler=_cmd_improve_evaluate)

    # cluster
    p_cluster = sub.add_parser("cluster", help="cluster failure traces and update replay cases")
    p_cluster.set_defaults(handler=_cmd_improve_cluster)

    # synthesize
    p_synth = sub.add_parser("synthesize", help="synthesize candidate challenger harnesses from defects")
    p_synth.set_defaults(handler=_cmd_improve_synthesize)

    # benchmark
    p_bench = sub.add_parser("benchmark", help="run tournament benchmarks on golden and replay suites")
    p_bench.set_defaults(handler=_cmd_improve_benchmark)

    # rollback
    p_rb = sub.add_parser("rollback", help="roll back active champion")
    p_rb.add_argument("--version", help="specific version to restore as champion")
    p_rb.set_defaults(handler=_cmd_improve_rollback)

    for p in (p_status, p_run, p_eval, p_cluster, p_synth, p_bench, p_rb):
        p.add_argument(
            "--project",
            default="hunterxjob",
            choices=["hunterxjob", "allo", "fidelis", "pedit", "agent_gang"],
            help="target application system (default: hunterxjob)",
        )
