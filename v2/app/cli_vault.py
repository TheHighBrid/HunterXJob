"""``./hunterx vault ...`` maintenance and seed commands."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnswerPolicy
from app.vault_store import seed_answer_vault_from_policy, upsert_answer


@contextmanager
def _session() -> Iterator[Session]:
    from app.db import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _cmd_vault_seed(args: argparse.Namespace) -> int:
    """Preview or seed Answer Vault records from JSON seed and Master Policy."""
    from app.answer_policy_compiler import get_seed_records, load_master_policy

    seed_records = get_seed_records()
    policy = load_master_policy()
    auto_items = [item for item in policy.items_list if item.mode == "auto" and item.answer]

    if not args.apply:
        print(f"=== ANSWER VAULT SEED PREVIEW ===")
        print(f"Direct user JSON seed records: {len(seed_records)}")
        for rec in seed_records:
            print(f"  [JSON] {rec.get('scope', 'global')}:{rec.get('key')} = {rec.get('value')}")
        print(f"\nMaster Answer Policy auto-answers: {len(auto_items)}")
        for it in auto_items[:15]:
            print(f"  [POLICY] {it.key} = {it.answer} (variants: {len(it.answer_variants)})")
        if len(auto_items) > 15:
            print(f"  ... and {len(auto_items) - 15} more policy auto-answers")
        print("\nPass --apply to write these answers into the active HunterXJob database.")
        return 0

    with _session() as db:
        res = seed_answer_vault_from_policy(
            db,
            force=args.force,
            include_master_policy=not args.json_only,
        )
        print(f"SUCCESS: Seeded {res['total']} answers into database.")
        print(f"  - Direct user JSON answers: {res['json_seeded']}")
        print(f"  - Master policy auto answers: {res['policy_seeded']}")
    return 0


def _cmd_vault_list(args: argparse.Namespace) -> int:
    """List all answers currently stored in the database."""
    with _session() as db:
        query = select(AnswerPolicy).order_by(AnswerPolicy.key.asc())
        if args.scope:
            query = query.where(AnswerPolicy.scope == args.scope)
        rows = db.execute(query).scalars().all()
        data = [
            {
                "key": r.key,
                "value": r.value,
                "scope": r.scope,
                "source": r.source,
                "confidence": r.confidence,
                "sensitive": r.sensitive,
                "note": r.note,
            }
            for r in rows
        ]
        _print(data)
        print(f"Total stored answer policies: {len(data)}", file=sys.stderr)
    return 0


def _cmd_vault_get(args: argparse.Namespace) -> int:
    """Retrieve an answer record by key and scope."""
    with _session() as db:
        row = db.execute(
            select(AnswerPolicy).where(AnswerPolicy.key == args.key, AnswerPolicy.scope == args.scope)
        ).scalar_one_or_none()
        if row is None:
            print(f"Answer for {args.scope}:{args.key} not found.", file=sys.stderr)
            return 1
        _print({
            "key": row.key,
            "value": row.value,
            "scope": row.scope,
            "source": row.source,
            "confidence": row.confidence,
            "sensitive": row.sensitive,
            "note": row.note,
        })
    return 0


def _cmd_vault_set(args: argparse.Namespace) -> int:
    """Upsert an answer policy."""
    with _session() as db:
        row = upsert_answer(
            db,
            key=args.key,
            value=args.value,
            source=args.source,
            scope=args.scope,
            confidence=args.confidence,
            sensitive=args.sensitive,
            note=args.note,
        )
        print(f"Saved: {row.scope}:{row.key} = {row.value}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    vault_parser = subparsers.add_parser("vault", help="Answer Vault management and policy seeding")
    v_sub = vault_parser.add_subparsers(dest="vault_subcommand", required=True)

    # vault seed
    p_seed = v_sub.add_parser("seed", help="preview or apply seed answers from JobTomatik vault docs")
    p_seed.add_argument("--apply", action="store_true", help="write seed answers into the active database")
    p_seed.add_argument("--force", action="store_true", help="overwrite existing answers with same key/scope")
    p_seed.add_argument("--json-only", action="store_true", help="seed only user JSON file, skip master YAML")
    p_seed.set_defaults(handler=_cmd_vault_seed)

    # vault list
    p_list = v_sub.add_parser("list", help="list all answers stored in the database")
    p_list.add_argument("--scope", default=None, help="filter by scope (e.g. global)")
    p_list.set_defaults(handler=_cmd_vault_list)

    # vault get
    p_get = v_sub.add_parser("get", help="get an answer record by key")
    p_get.add_argument("key", help="canonical key")
    p_get.add_argument("--scope", default="global", help="scope (default: global)")
    p_get.set_defaults(handler=_cmd_vault_get)

    # vault set
    p_set = v_sub.add_parser("set", help="set or update an answer policy")
    p_set.add_argument("key", help="canonical key")
    p_set.add_argument("value", help="answer value")
    p_set.add_argument("--scope", default="global", help="scope (default: global)")
    p_set.add_argument("--source", default="user", help="source (user, policy, etc.)")
    p_set.add_argument("--confidence", type=float, default=1.0, help="confidence (default: 1.0)")
    p_set.add_argument("--sensitive", action="store_true", help="mark answer as sensitive")
    p_set.add_argument("--note", default="", help="context note")
    p_set.set_defaults(handler=_cmd_vault_set)
