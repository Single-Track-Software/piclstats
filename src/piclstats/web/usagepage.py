"""Data for the admin Usage page."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.quality.diagrams import sparkline_svg

_HUMAN = "NOT is_bot AND status < 400"


def _rows(session: Session, sql: str, **p: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in session.execute(text(sql), p).all()]


def page(session: Session, days: int = 30) -> dict[str, Any]:
    totals = _rows(
        session,
        """
        SELECT
            count(*) FILTER (WHERE NOT is_bot AND status < 400 AND ts >= now() - interval '1 day') AS views_1d,
            count(DISTINCT visitor) FILTER (WHERE NOT is_bot AND status < 400 AND ts >= now() - interval '1 day') AS visitors_1d,
            count(*) FILTER (WHERE NOT is_bot AND status < 400 AND ts >= now() - interval '7 days') AS views_7d,
            count(DISTINCT visitor) FILTER (WHERE NOT is_bot AND status < 400 AND ts >= now() - interval '7 days') AS visitors_7d,
            count(*) FILTER (WHERE NOT is_bot AND status < 400 AND ts >= now() - interval '30 days') AS views_30d,
            count(DISTINCT visitor) FILTER (WHERE NOT is_bot AND status < 400 AND ts >= now() - interval '30 days') AS visitors_30d,
            count(DISTINCT user_id) FILTER (WHERE NOT is_bot AND status < 400 AND ts >= now() - interval '7 days' AND user_id IS NOT NULL) AS users_7d,
            count(*) FILTER (WHERE is_bot AND ts >= now() - interval '7 days') AS bot_views_7d
        FROM page_views WHERE ts >= now() - interval '30 days'
        """,
    )[0]
    daily = _rows(
        session,
        f"""
        SELECT d::date AS day,
               COALESCE(v.views, 0) AS views, COALESCE(v.visitors, 0) AS visitors
        FROM generate_series((now() - make_interval(days => :days - 1))::date, now()::date, '1 day') d
        LEFT JOIN (
            SELECT ts::date AS day, count(*) AS views, count(DISTINCT visitor) AS visitors
            FROM page_views WHERE {_HUMAN} AND ts >= now() - make_interval(days => :days)
            GROUP BY ts::date
        ) v ON v.day = d::date
        ORDER BY d
        """,
        days=days,
    )
    routes = _rows(
        session,
        f"""
        SELECT route, count(*) AS views, count(DISTINCT visitor) AS visitors,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)) AS p50_ms,
               round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)) AS p95_ms
        FROM page_views WHERE {_HUMAN} AND ts >= now() - make_interval(days => :days)
        GROUP BY route ORDER BY views DESC
        """,
        days=days,
    )
    riders = _rows(
        session,
        f"""
        SELECT ri.id, ri.name, ri.team, count(*) AS views, count(DISTINCT p.visitor) AS visitors
        FROM page_views p JOIN riders ri ON ri.id = p.entity::int
        WHERE p.route = 'rider' AND {_HUMAN} AND p.entity ~ '^[0-9]+$'
          AND p.ts >= now() - make_interval(days => :days)
        GROUP BY ri.id, ri.name, ri.team ORDER BY views DESC, visitors DESC LIMIT 15
        """,
        days=days,
    )
    teams = _rows(
        session,
        f"""
        SELECT entity AS team, count(*) AS views, count(DISTINCT visitor) AS visitors
        FROM page_views WHERE route = 'team' AND {_HUMAN}
          AND ts >= now() - make_interval(days => :days)
        GROUP BY entity ORDER BY views DESC, visitors DESC LIMIT 15
        """,
        days=days,
    )
    users = _rows(
        session,
        f"""
        SELECT u.id, u.email, u.name, u.role, u.last_login_at,
               max(p.ts) AS last_seen, count(p.id) AS views,
               count(p.id) FILTER (WHERE p.route = 'staging') AS staging_views,
               count(p.id) FILTER (WHERE p.route = 'forecast') AS forecast_views
        FROM users u LEFT JOIN page_views p ON p.user_id = u.id AND {_HUMAN}
            AND p.ts >= now() - make_interval(days => :days)
        WHERE u.is_active
        GROUP BY u.id ORDER BY last_seen DESC NULLS LAST, u.email
        """,
        days=days,
    )
    referrers = _rows(
        session,
        f"""
        SELECT referrer, count(*) AS views, count(DISTINCT visitor) AS visitors
        FROM page_views WHERE referrer IS NOT NULL AND {_HUMAN}
          AND ts >= now() - make_interval(days => :days)
        GROUP BY referrer ORDER BY views DESC LIMIT 15
        """,
        days=days,
    )
    recent = _rows(
        session,
        f"""
        SELECT p.ts, p.route, p.path, p.query, p.status, p.duration_ms, p.visitor, u.email
        FROM page_views p LEFT JOIN users u ON u.id = p.user_id
        WHERE {_HUMAN}
        ORDER BY p.ts DESC LIMIT 60
        """,
    )
    return {
        "days": days,
        "totals": totals,
        "daily": daily,
        "spark_views": sparkline_svg([float(d["views"]) for d in daily]),
        "spark_visitors": sparkline_svg([float(d["visitors"]) for d in daily], "#16a34a"),
        "routes": routes,
        "riders": riders,
        "teams": teams,
        "users": users,
        "referrers": referrers,
        "recent": recent,
    }
