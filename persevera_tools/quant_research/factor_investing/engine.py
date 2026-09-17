"""Factor investing backtest orchestration."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from .config import BacktestConfig
from .data import build_rebalance_dates, densify_prices, load_backtest_panels
from .definitions import (
    get_higher_is_better_map,
    load_factor_definitions,
    resolve_component_names,
)
from .portfolio import scores_to_weights
from .result import (
    BacktestDiagnostics,
    BacktestResult,
    build_summary,
    summarize_costs,
    summarize_diagnostics,
)
from .scoring import calculate_factor_exposure, snapshot_components, snapshot_series


def _prices_wide(price_panel: pd.DataFrame, price_field: str) -> pd.DataFrame:
    """Convert MultiIndex price panel to date × ticker wide frame."""
    if price_panel.empty:
        return pd.DataFrame()
    if isinstance(price_panel.columns, pd.MultiIndex):
        try:
            wide = price_panel.xs(price_field, level=-1, axis=1)
        except KeyError:
            return pd.DataFrame()
        return wide.sort_index()
    return price_panel.sort_index()


def _daily_portfolio_return(
    weights: pd.Series,
    asset_returns: pd.Series,
    *,
    as_of: Optional[pd.Timestamp] = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Return portfolio return and diagnostic events for missing prices / renorm."""
    events: list[dict[str, Any]] = []
    if weights is None or weights.empty:
        return float("nan"), events

    w_held = weights[weights.notna() & (weights != 0.0)]
    if w_held.empty:
        return float("nan"), events

    r = asset_returns.reindex(w_held.index)
    for code in r[r.isna()].index:
        events.append(
            {
                "date": as_of,
                "code": str(code),
                "event": "price_gap",
                "detail": "held name with missing return after price ffill",
            }
        )

    aligned = pd.concat([w_held.rename("w"), r.rename("r")], axis=1).dropna()
    if aligned.empty:
        return float("nan"), events

    dropped = sorted(set(w_held.index) - set(aligned.index))
    if dropped:
        events.append(
            {
                "date": as_of,
                "code": ",".join(str(c) for c in dropped),
                "event": "renorm",
                "detail": f"dropped {len(dropped)} name(s); renormalized remaining legs",
            }
        )

    w = aligned["w"]
    rets = aligned["r"]
    target_long = float(w_held[w_held > 0].sum())
    target_short = float(w_held[w_held < 0].sum())
    w_adj = pd.Series(0.0, index=w.index, dtype=float)

    long_w = w[w > 0]
    short_w = w[w < 0]
    if not long_w.empty and target_long != 0:
        w_adj.loc[long_w.index] = long_w / long_w.sum() * target_long
    if not short_w.empty and target_short != 0:
        w_adj.loc[short_w.index] = short_w / short_w.sum() * target_short
    if long_w.empty and short_w.empty:
        return float("nan"), events
    return float((w_adj * rets).sum()), events


def _traded_notional(prev_w: Optional[pd.Series], curr_w: pd.Series) -> float:
    """Two-way traded notional as a fraction of NAV: ``sum(|Δw|)``.

    Missing previous book (first rebalance) is treated as a deployment from
    cash, so this equals ``sum(|curr_w|)``.
    """
    curr = (
        curr_w.fillna(0.0)
        if curr_w is not None and not curr_w.empty
        else pd.Series(dtype=float)
    )
    if prev_w is None or prev_w.empty:
        return float(curr.abs().sum())
    aligned = pd.concat(
        [prev_w.fillna(0.0).rename("prev"), curr.rename("curr")],
        axis=1,
    ).fillna(0.0)
    return float((aligned["curr"] - aligned["prev"]).abs().sum())


def _turnover(prev_w: Optional[pd.Series], curr_w: pd.Series) -> float:
    """One-way turnover = 0.5 * sum(|Δw|)."""
    if prev_w is None or prev_w.empty:
        return float("nan")
    return 0.5 * _traded_notional(prev_w, curr_w)


def _rebal_row(
    dt: pd.Timestamp,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one diagnostics row for a formed rebalance date."""
    universe: pd.Index = payload["universe"]
    cross: pd.DataFrame = payload["cross"]
    scores: pd.Series = payload["scores"]
    weights: pd.Series = payload["weights"]
    components: Sequence[str] = payload["components"]
    prev_weights: Optional[pd.Series] = payload["prev_weights"]

    n_universe = int(len(universe))
    missing_all = cross.reindex(universe).isna().all(axis=1)
    traded = _traded_notional(prev_weights, weights)
    row: dict[str, Any] = {
        "date": dt,
        "n_universe": n_universe,
        "n_scored": int(scores.dropna().shape[0]),
        "n_long": int((weights > 0).sum()),
        "n_short": int((weights < 0).sum()),
        "n_missing_components": int(missing_all.sum()) if n_universe else 0,
        "turnover": _turnover(prev_weights, weights),
        "traded_notional": traded,
        "trading_cost": traded * float(payload.get("trading_cost_bps", 0.0)) / 10_000.0,
    }
    cross_u = cross.reindex(universe)
    for comp in components:
        key = f"cov__{comp}"
        row[key] = (
            float(cross_u[comp].notna().mean())
            if comp in cross_u.columns and n_universe > 0
            else float("nan")
        )
    return row


def _dropped_at_rebal_events(
    prev_w: pd.Series,
    curr_w: pd.Series,
    dt: pd.Timestamp,
) -> list[dict[str, Any]]:
    prev_held = set(prev_w[prev_w.notna() & (prev_w != 0.0)].index)
    curr_held = set(curr_w[curr_w.notna() & (curr_w != 0.0)].index)
    return [
        {
            "date": dt,
            "code": str(code),
            "event": "dropped_at_rebal",
            "detail": "held at prior rebalance; weight zero after this rebalance",
        }
        for code in sorted(prev_held - curr_held)
    ]


def _form_rebalance_books(
    config: BacktestConfig,
    *,
    rebalance_dates: pd.DatetimeIndex,
    adtv_panel: pd.DataFrame,
    component_panel: pd.DataFrame,
    components: tuple[str, ...],
    higher_is_better: Mapping[str, bool],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """Form scores/weights and collect rebalance diagnostics + drop events."""
    score_rows: list[pd.Series] = []
    weight_rows: list[pd.Series] = []
    rebal_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    prev_weights: Optional[pd.Series] = None

    for dt in rebalance_dates:
        adtv = snapshot_series(adtv_panel, dt, config.adtv_field)
        if adtv.empty:
            continue
        universe = adtv[adtv >= config.adtv_min].dropna().index
        if len(universe) < config.min_names:
            continue

        cross = snapshot_components(component_panel, dt, components)
        if cross.empty:
            continue
        cross_u = cross.reindex(universe)
        cross_scored = cross_u.dropna(how="all")
        if len(cross_scored) < config.min_names:
            continue

        scores = calculate_factor_exposure(
            cross_scored,
            factor_name=config.style or "factor_score",
            higher_is_better_map=higher_is_better,
            zR=config.zR,
            zC=config.zC,
        ).dropna()
        if len(scores) < config.min_names:
            continue

        weights = scores_to_weights(
            scores,
            construction=config.construction,
            selection_mode=config.selection_mode,
            quantile=config.quantile,
            top_n=config.top_n,
        )
        if weights.empty:
            continue

        rebal_rows.append(
            _rebal_row(
                dt,
                {
                    "universe": universe,
                    "cross": cross_u,
                    "scores": scores,
                    "weights": weights,
                    "components": components,
                    "prev_weights": prev_weights,
                    "trading_cost_bps": config.trading_cost_bps,
                },
            )
        )
        if prev_weights is not None:
            event_rows.extend(_dropped_at_rebal_events(prev_weights, weights, dt))

        score_rows.append(scores.rename(dt))
        weight_rows.append(weights.rename(dt))
        prev_weights = weights

    if not weight_rows:
        raise ValueError(
            "No valid rebalance portfolios were formed; "
            "relax adtv_min / min_names or check data coverage"
        )

    scores_df = pd.concat(score_rows, axis=1).T
    scores_df.index = pd.DatetimeIndex(scores_df.index)
    weights_df = pd.concat(weight_rows, axis=1).T.fillna(0.0)
    weights_df.index = pd.DatetimeIndex(weights_df.index)
    rebals_df = pd.DataFrame(rebal_rows)
    if not rebals_df.empty:
        rebals_df = rebals_df.set_index("date").sort_index()
        rebals_df.index = pd.DatetimeIndex(rebals_df.index)
    return scores_df, weights_df, rebals_df, event_rows


def _simulate_holdings_pnl(
    *,
    prices: pd.DataFrame,
    weights_df: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    initial_nav: float,
    borrow_rate: float = 0.0,
    trading_cost_by_date: Optional[Mapping[Any, float]] = None,
) -> tuple[pd.Series, pd.Series, pd.DataFrame, list[dict[str, Any]], int, int]:
    """Simulate daily P&L from rebalance weights; return nav/returns/costs/events/counters."""
    trading_index = prices.index[(prices.index >= start_date) & (prices.index <= end_date)]
    daily_weights = weights_df.reindex(trading_index).ffill()
    asset_returns = prices.pct_change(fill_method=None)
    cost_map = {
        pd.Timestamp(k).normalize(): float(v)
        for k, v in dict(trading_cost_by_date or {}).items()
        if v is not None and pd.notna(v)
    }

    strategy_returns = pd.Series(index=trading_index, dtype=float, name="strategy_return")
    trading_costs = pd.Series(0.0, index=trading_index, dtype=float, name="trading_cost")
    borrow_costs = pd.Series(0.0, index=trading_index, dtype=float, name="borrow_cost")
    event_rows: list[dict[str, Any]] = []
    n_days_holdings = 0
    n_days_renorm = 0

    for dt in trading_index:
        pos = trading_index.get_loc(dt)
        dt_norm = pd.Timestamp(dt).normalize()
        trading_cost = float(cost_map.get(dt_norm, 0.0))

        w = pd.Series(dtype=float)
        if isinstance(pos, int) and pos > 0:
            w_raw = daily_weights.loc[trading_index[pos - 1]]
            if isinstance(w_raw, pd.Series):
                w = w_raw[w_raw.notna() & (w_raw != 0.0)]

        borrow_cost = 0.0
        port_ret = float("nan")
        if not w.empty:
            n_days_holdings += 1
            port_ret, day_events = _daily_portfolio_return(
                w, asset_returns.loc[dt], as_of=dt
            )
            if any(e["event"] == "renorm" for e in day_events):
                n_days_renorm += 1
            event_rows.extend(day_events)
            if borrow_rate:
                short_leg = w[w < 0]
                if not short_leg.empty:
                    borrow_cost = float((-short_leg).sum()) * float(borrow_rate) / 252.0

        if pd.isna(port_ret):
            if w.empty and trading_cost == 0.0:
                strategy_returns.loc[dt] = float("nan")
                continue
            port_ret = 0.0

        trading_costs.loc[dt] = trading_cost
        borrow_costs.loc[dt] = borrow_cost
        strategy_returns.loc[dt] = float(port_ret) - trading_cost - borrow_cost

    first_valid = strategy_returns.first_valid_index()
    if first_valid is not None:
        strategy_returns.loc[first_valid:] = strategy_returns.loc[first_valid:].fillna(0.0)
        nav = (
            (1.0 + strategy_returns.loc[first_valid:].fillna(0.0)).cumprod() * initial_nav
        ).reindex(trading_index)
    else:
        nav = pd.Series(dtype=float, index=trading_index)
    nav.name = "nav"
    costs_df = pd.DataFrame(
        {"trading_cost": trading_costs, "borrow_cost": borrow_costs}
    )
    costs_df.index.name = "date"
    return nav, strategy_returns, costs_df, event_rows, n_days_holdings, n_days_renorm


def _gross_nav_from_net(
    net_returns: pd.Series,
    costs_df: pd.DataFrame,
    *,
    initial_nav: float,
) -> pd.Series:
    """Rebuild a cost-free NAV by adding daily trading and borrow drag back."""
    first_valid = net_returns.first_valid_index()
    if first_valid is None:
        return pd.Series(dtype=float, name="gross_nav")

    trading = (
        costs_df["trading_cost"].reindex(net_returns.index).fillna(0.0)
        if not costs_df.empty and "trading_cost" in costs_df.columns
        else 0.0
    )
    borrow = (
        costs_df["borrow_cost"].reindex(net_returns.index).fillna(0.0)
        if not costs_df.empty and "borrow_cost" in costs_df.columns
        else 0.0
    )
    gross = (net_returns.fillna(0.0) + trading + borrow).loc[first_valid:]
    nav = (1.0 + gross.fillna(0.0)).cumprod() * initial_nav
    nav = nav.reindex(net_returns.index)
    nav.name = "gross_nav"
    return nav


def _build_diagnostics(
    rebals_df: pd.DataFrame,
    event_rows: list[dict[str, Any]],
    costs_df: pd.DataFrame,
    *,
    n_days_holdings: int,
    n_days_renorm: int,
) -> tuple[BacktestDiagnostics, dict[str, Any]]:
    events_df = pd.DataFrame(event_rows, columns=["date", "code", "event", "detail"])
    if not events_df.empty:
        events_df["date"] = pd.to_datetime(events_df["date"])
        events_df = events_df.sort_values(["date", "event", "code"]).reset_index(drop=True)

    diagnostics = BacktestDiagnostics(
        rebals=rebals_df, events=events_df, costs=costs_df
    )
    diag_summary = summarize_diagnostics(diagnostics)
    if n_days_holdings > 0:
        diag_summary["pct_days_with_renorm"] = float(n_days_renorm / n_days_holdings)
    diag_summary.pop("_renorm_dates", None)
    return diagnostics, diag_summary


def run_backtest(config: BacktestConfig) -> BacktestResult:
    """
    Run a factor backtest end-to-end.

    1. Resolve components from Fibery style tags (or explicit mnemonics)
    2. Load historical ``factor_zoo`` panels via ``get_descriptors``
    3. On each rebalance date: ADTV filter → PIT snapshot → score → weights
    4. Hold weights until the next rebalance; compute daily P&L from ``price_field``
    5. Debit trading costs on rebalance closes and borrow on overnight shorts
    """
    definitions = load_factor_definitions()
    components = resolve_component_names(
        style=config.style,
        components=tuple(config.components) if config.components else None,
        definitions=definitions,
    )
    higher_is_better = get_higher_is_better_map(definitions)
    panels = load_backtest_panels(config, components)

    prices_raw = _prices_wide(panels["prices"], config.price_field)
    if prices_raw.empty:
        raise ValueError(f"No price data found for field {config.price_field!r}")

    prices = densify_prices(
        prices_raw,
        start=config.start_date,
        end=config.end_date,
        ffill_limit=config.price_ffill_limit,
    )
    rebalance_dates = build_rebalance_dates(config, prices.index)
    if rebalance_dates.empty:
        raise ValueError("No rebalance dates in the requested window")

    scores_df, weights_df, rebals_df, rebal_events = _form_rebalance_books(
        config,
        rebalance_dates=rebalance_dates,
        adtv_panel=panels["adtv"],
        component_panel=panels["components"],
        components=components,
        higher_is_better=higher_is_better,
    )
    nav, strategy_returns, costs_df, hold_events, n_days_holdings, n_days_renorm = (
        _simulate_holdings_pnl(
            prices=prices,
            weights_df=weights_df,
            start_date=config.start_date,
            end_date=config.end_date,
            initial_nav=config.initial_nav,
            borrow_rate=config.borrow_rate,
            trading_cost_by_date=(
                rebals_df["trading_cost"].to_dict()
                if not rebals_df.empty and "trading_cost" in rebals_df.columns
                else None
            ),
        )
    )
    diagnostics, diag_summary = _build_diagnostics(
        rebals_df,
        rebal_events + hold_events,
        costs_df,
        n_days_holdings=n_days_holdings,
        n_days_renorm=n_days_renorm,
    )

    summary = build_summary(nav.dropna(), risk_free_rate=config.risk_free_rate)
    summary.update(
        {
            "construction": config.construction,
            "style": config.style,
            "components": list(components),
            "n_rebals": int(len(weights_df)),
            "selection_mode": config.selection_mode,
            "quantile": config.quantile,
            "top_n": config.top_n,
            "adtv_min": config.adtv_min,
            "denomination": config.denomination,
            "trading_cost_bps": config.trading_cost_bps,
            "borrow_rate": config.borrow_rate,
        }
    )
    summary.update(diag_summary)
    n_obs = int(summary.get("n_obs") or 0)
    summary.update(summarize_costs(costs_df, n_obs=n_obs))
    gross_nav = _gross_nav_from_net(
        strategy_returns, costs_df, initial_nav=config.initial_nav
    )
    if not gross_nav.empty:
        gross_stats = build_summary(gross_nav.dropna(), risk_free_rate=config.risk_free_rate)
        summary["gross_annualized_return"] = gross_stats["annualized_return"]
    else:
        summary["gross_annualized_return"] = float("nan")

    return BacktestResult(
        nav=nav,
        returns=strategy_returns,
        weights=weights_df,
        scores=scores_df,
        summary=summary,
        diagnostics=diagnostics,
        config=config,
        components=components,
    )
