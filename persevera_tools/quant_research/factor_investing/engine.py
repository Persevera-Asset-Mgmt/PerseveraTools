"""Factor investing backtest orchestration."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from ...utils.dates import get_holidays
from .config import BacktestConfig
from .data import (
    build_rebalance_dates,
    densify_prices,
    index_members_at,
    load_backtest_panels,
    load_reference_series,
    most_liquid_per_radical,
)
from .definitions import (
    get_component_max_age,
    get_higher_is_better_map,
    load_factor_definitions,
    resolve_component_names,
)
from .portfolio import beta_neutralize, scores_to_weights
from .result import (
    BacktestDiagnostics,
    BacktestResult,
    build_summary,
    summarize_costs,
    summarize_diagnostics,
)
from .scoring import (
    apply_max_age,
    component_zscores,
    coverage_mask,
    last_values_with_age,
    snapshot_series,
)


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

    w_adj = _rescale_legs(w_held, aligned.index)
    return float((w_adj * aligned["r"]).sum()), events


def _rescale_legs(weights: pd.Series, kept: pd.Index) -> pd.Series:
    """Weights on ``kept``, each leg rescaled to its gross in ``weights``."""
    sub = weights.reindex(kept).fillna(0.0)
    out = pd.Series(0.0, index=kept, dtype=float)
    for full_leg, leg in (
        (weights[weights > 0], sub[sub > 0]),
        (weights[weights < 0], sub[sub < 0]),
    ):
        if not leg.empty and float(full_leg.sum()) != 0:
            out.loc[leg.index] = leg / leg.sum() * float(full_leg.sum())
    return out


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
        "n_low_coverage": int(payload.get("n_low_coverage", 0)),
        "n_dropped_share_class": int(payload.get("n_dropped_share_class", 0)),
        "turnover": _turnover(prev_weights, weights),
        "traded_notional": traded,
        "trading_cost": traded * float(payload.get("trading_cost_bps", 0.0)) / 10_000.0,
    }
    row.update(payload.get("beta_info") or {})
    if payload.get("n_index_members") is not None:
        row["n_index_members"] = int(payload["n_index_members"])
    cross_u = cross.reindex(universe)
    stale: pd.Series = payload.get("stale", pd.Series(dtype=float))
    for comp in components:
        key = f"cov__{comp}"
        row[key] = (
            float(cross_u[comp].notna().mean())
            if comp in cross_u.columns and n_universe > 0
            else float("nan")
        )
        row[f"stale__{comp}"] = int(stale.get(comp, 0))
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


def _traded_names(prices: pd.DataFrame, as_of: pd.Timestamp) -> pd.Index:
    """Tickers with a price on the last session at or before ``as_of``.

    ``prices`` is the densified close panel, already forward-filled for at most
    ``price_ffill_limit`` business days. A name that stopped trading outside
    that window is missing on this row.
    """
    if prices.empty:
        return pd.Index([])
    hist = prices.loc[:as_of]
    if hist.empty:
        return pd.Index([])
    row = hist.iloc[-1]
    return row[row.notna()].index


def _form_rebalance_books(
    config: BacktestConfig,
    *,
    rebalance_dates: pd.DatetimeIndex,
    adtv_panel: pd.DataFrame,
    component_panel: pd.DataFrame,
    prices: pd.DataFrame,
    components: tuple[str, ...],
    higher_is_better: Mapping[str, bool],
    max_age_days: Optional[Mapping[str, int]] = None,
    beta_panel: Optional[pd.DataFrame] = None,
    membership: Optional[pd.DataFrame] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]], pd.DataFrame]:
    """Form scores/weights and collect rebalance diagnostics + drop events.

    Returns scores, weights, rebalance diagnostics, events and the
    sign-aligned component z-scores (indexed by ``(date, code)``).

    A name is investable only if it still has a price inside ``price_ffill_limit``
    and its ADTV print is inside that same limit and at least ``adtv_min``.
    Factor values older than their ``max_age_days`` limit count as missing.
    With ``config.universe_index`` it must also be an index member on the
    latest stored session on or before the rebalance (``membership``). With
    ``config.beta_neutral`` the legs are resized with betas from ``beta_panel``
    under the ``config.beta_field`` limit in ``max_age_days``; a stale beta is
    missing, so ``beta_neutralize`` fills it with the median.
    """
    score_rows: list[pd.Series] = []
    weight_rows: list[pd.Series] = []
    rebal_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    component_rows: list[pd.DataFrame] = []
    prev_weights: Optional[pd.Series] = None

    for dt in rebalance_dates:
        traded = _traded_names(prices, dt)
        n_index_members: Optional[int] = None
        if config.universe_index:
            members = index_members_at(membership, dt, config.index_max_age_days)
            n_index_members = len(members)
            traded = traded.intersection(members)
        if traded.empty:
            continue
        adtv = snapshot_series(
            adtv_panel,
            dt,
            config.adtv_field,
            max_age=config.price_ffill_limit,
        )
        if adtv.empty:
            continue
        liquid = adtv.reindex(traded)
        universe = liquid[liquid >= config.adtv_min].dropna().index
        if len(universe) < config.min_names:
            continue

        last_values, ages = last_values_with_age(component_panel, dt, components)
        cross = apply_max_age(last_values, ages, max_age_days)
        if cross.empty:
            continue
        cross_u = cross.reindex(universe)
        stale = (
            (last_values.notna() & cross.isna()).reindex(universe).fillna(False).sum()
        )
        covered = coverage_mask(cross_u, config.min_component_coverage)
        has_any = cross_u.notna().any(axis=1)
        n_low_coverage = int((has_any & ~covered).sum())
        cross_scored = cross_u.loc[covered]
        n_dropped_share_class = 0
        if config.one_class_per_issuer:
            keep = most_liquid_per_radical(liquid.reindex(cross_scored.index))
            n_dropped_share_class = len(cross_scored) - len(keep)
            cross_scored = cross_scored.loc[keep]
        if len(cross_scored) < config.min_names:
            continue

        zs = component_zscores(
            cross_scored,
            higher_is_better_map=higher_is_better,
            zR=config.zR,
            zC=config.zC,
        )
        scores = zs.mean(axis=1, skipna=True).rename(config.style or "factor_score").dropna()
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

        beta_info: Optional[dict[str, float]] = None
        if config.beta_neutral:
            beta_last, beta_ages = last_values_with_age(
                beta_panel if beta_panel is not None else pd.DataFrame(),
                dt,
                (config.beta_field,),
            )
            beta_cross = apply_max_age(beta_last, beta_ages, max_age_days)
            if config.beta_field in beta_cross.columns:
                betas = beta_cross[config.beta_field].reindex(scores.index)
                n_stale_beta = int(
                    (
                        beta_last[config.beta_field].notna()
                        & beta_cross[config.beta_field].isna()
                    )
                    .reindex(weights.index)
                    .fillna(False)
                    .sum()
                )
            else:
                betas = pd.Series(float("nan"), index=scores.index)
                n_stale_beta = 0
            weights, beta_info = beta_neutralize(weights, betas)
            beta_info["n_stale_beta"] = n_stale_beta
            if not (beta_info["beta_long"] > 0 and beta_info["beta_short"] > 0):
                event_rows.append(
                    {
                        "date": dt,
                        "code": "",
                        "event": "beta_neutral_skipped",
                        "detail": (
                            f"leg betas long={beta_info['beta_long']:.3f} "
                            f"short={beta_info['beta_short']:.3f}; kept dollar-neutral"
                        ),
                    }
                )

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
                    "n_dropped_share_class": n_dropped_share_class,
                    "n_low_coverage": n_low_coverage,
                    "stale": stale,
                    "beta_info": beta_info,
                    "n_index_members": n_index_members,
                },
            )
        )
        if prev_weights is not None:
            event_rows.extend(_dropped_at_rebal_events(prev_weights, weights, dt))

        zs = zs.loc[scores.index]
        zs.index = pd.MultiIndex.from_product([[dt], zs.index], names=["date", "code"])
        component_rows.append(zs)
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
    components_df = pd.concat(component_rows).sort_index()
    return scores_df, weights_df, rebals_df, event_rows, components_df


def _drift_session(
    weights: pd.Series,
    asset_returns: pd.Series,
    *,
    as_of: Optional[pd.Timestamp] = None,
) -> tuple[pd.Series, pd.Series, float, list[dict[str, Any]]]:
    """
    One session for drifting holdings: close names without a return, then P&L.

    A held name with no return (price gone past ``price_ffill_limit``) leaves the
    book for good, as if sold at its last price; its weight goes to the rest of
    its leg. Returns the start-of-session weights, their returns, the portfolio
    return and diagnostic events.
    """
    events: list[dict[str, Any]] = []
    r = asset_returns.reindex(weights.index)
    missing = r[r.isna()].index
    if len(missing):
        for code in missing:
            events.append(
                {
                    "date": as_of,
                    "code": str(code),
                    "event": "price_gap",
                    "detail": "held name with missing return after price ffill; position closed",
                }
            )
        events.append(
            {
                "date": as_of,
                "code": ",".join(str(c) for c in sorted(missing)),
                "event": "renorm",
                "detail": f"dropped {len(missing)} name(s); renormalized remaining legs",
            }
        )
        weights = _rescale_legs(weights, r.dropna().index)
        weights = weights[weights != 0.0]
        r = r.reindex(weights.index)
    if weights.empty:
        return weights, r, float("nan"), events
    return weights, r, float((weights * r).sum()), events


def _simulate_holdings_pnl(
    *,
    prices: pd.DataFrame,
    weights_df: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    initial_nav: float,
    borrow_rate: float = 0.0,
    trading_cost_bps: float = 0.0,
    weight_drift: bool = True,
) -> tuple[
    pd.Series, pd.Series, pd.DataFrame, list[dict[str, Any]], int, int, dict[pd.Timestamp, float]
]:
    """
    Simulate daily P&L from rebalance weights.

    ``weight_drift=True`` holds the book between rebalances: weights move with
    returns, the rebalance trades from the drifted weights to the new target,
    and borrow accrues on the drifted short leg. ``False`` resets the book to its
    target every session (no cost for that), trading target to target.

    Returns nav, returns, daily costs, events, holding-day and renorm-day
    counters, and the two-way traded notional ``sum(|Δw|)`` on each rebalance.
    """
    trading_index = prices.index[(prices.index >= start_date) & (prices.index <= end_date)]
    daily_weights = weights_df.reindex(trading_index).ffill()
    asset_returns = prices.pct_change(fill_method=None)
    rebalances = set(weights_df.index)

    strategy_returns = pd.Series(index=trading_index, dtype=float, name="strategy_return")
    trading_costs = pd.Series(0.0, index=trading_index, dtype=float, name="trading_cost")
    borrow_costs = pd.Series(0.0, index=trading_index, dtype=float, name="borrow_cost")
    event_rows: list[dict[str, Any]] = []
    traded_by_date: dict[pd.Timestamp, float] = {}
    n_days_holdings = 0
    n_days_renorm = 0
    holdings = pd.Series(dtype=float)  # drift: weights after the previous close
    prev_target: Optional[pd.Series] = None

    for dt in trading_index:
        pos = trading_index.get_loc(dt)

        w = pd.Series(dtype=float)
        if weight_drift:
            w = holdings
        elif isinstance(pos, int) and pos > 0:
            w_raw = daily_weights.loc[trading_index[pos - 1]]
            if isinstance(w_raw, pd.Series):
                w = w_raw[w_raw.notna() & (w_raw != 0.0)]

        borrow_cost = 0.0
        port_ret = float("nan")
        end_weights = pd.Series(dtype=float)
        if not w.empty:
            n_days_holdings += 1
            if weight_drift:
                w, r, port_ret, day_events = _drift_session(
                    w, asset_returns.loc[dt], as_of=dt
                )
            else:
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
            if weight_drift and not w.empty:
                end_weights = w * (1.0 + r) / (1.0 + port_ret - borrow_cost)

        trading_cost = 0.0
        if dt in rebalances:
            target = weights_df.loc[dt]
            target = target[target.notna() & (target != 0.0)]
            reference = end_weights if weight_drift else prev_target
            traded = _traded_notional(
                reference if reference is not None and not reference.empty else None,
                target,
            )
            traded_by_date[pd.Timestamp(dt)] = traded
            trading_cost = traded * float(trading_cost_bps) / 10_000.0
            prev_target = target
            holdings = target
        elif weight_drift:
            holdings = end_weights

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
    return (
        nav,
        strategy_returns,
        costs_df,
        event_rows,
        n_days_holdings,
        n_days_renorm,
        traded_by_date,
    )


def _apply_realized_trades(
    rebals_df: pd.DataFrame,
    traded_by_date: Mapping[pd.Timestamp, float],
    trading_cost_bps: float,
) -> pd.DataFrame:
    """Overwrite rebalance turnover/notional/cost with what the simulation traded."""
    if rebals_df.empty or not traded_by_date:
        return rebals_df
    out = rebals_df.copy()
    traded = pd.Series(traded_by_date, dtype=float).reindex(out.index)
    has = traded.notna()
    out.loc[has, "traded_notional"] = traded[has]
    out.loc[has, "trading_cost"] = traded[has] * float(trading_cost_bps) / 10_000.0
    if "turnover" in out.columns:
        # First book is a deployment from cash: turnover stays NaN, as before.
        keep_nan = out["turnover"].isna()
        out.loc[has & ~keep_nan, "turnover"] = 0.5 * traded[has & ~keep_nan]
    return out


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
    3. On each rebalance date: recent price and ADTV filter → PIT snapshot
       (stale components blanked) → score → weights
    4. Hold the book until the next rebalance (weights drift with returns unless
       ``weight_drift=False``); compute daily P&L from ``price_field``
    5. Debit trading costs on rebalance closes and borrow on overnight shorts
    """
    definitions = load_factor_definitions()
    components = resolve_component_names(
        style=config.style,
        components=tuple(config.components) if config.components else None,
        definitions=definitions,
    )
    higher_is_better = get_higher_is_better_map(definitions)
    max_age_days = get_component_max_age(
        tuple(dict.fromkeys(components + ((config.beta_field,) if config.beta_neutral else ()))),
        definitions,
        max_age_by_frequency=config.max_age_by_frequency,
        overrides=config.component_max_age_days,
    )
    panels = load_backtest_panels(config, components)

    prices_raw = _prices_wide(panels["prices"], config.price_field)
    if prices_raw.empty:
        raise ValueError(f"No price data found for field {config.price_field!r}")

    prices = densify_prices(
        prices_raw,
        start=config.start_date,
        end=config.end_date,
        ffill_limit=config.price_ffill_limit,
        holidays=get_holidays(),
    )
    rebalance_dates = build_rebalance_dates(config, prices.index)
    if rebalance_dates.empty:
        raise ValueError("No rebalance dates in the requested window")

    scores_df, weights_df, rebals_df, rebal_events, components_df = _form_rebalance_books(
        config,
        rebalance_dates=rebalance_dates,
        adtv_panel=panels["adtv"],
        component_panel=panels["components"],
        prices=prices,
        components=components,
        higher_is_better=higher_is_better,
        max_age_days=max_age_days,
        beta_panel=panels.get("beta"),
        membership=panels.get("membership"),
    )
    (
        nav,
        strategy_returns,
        costs_df,
        hold_events,
        n_days_holdings,
        n_days_renorm,
        traded_by_date,
    ) = _simulate_holdings_pnl(
        prices=prices,
        weights_df=weights_df,
        start_date=config.start_date,
        end_date=config.end_date,
        initial_nav=config.initial_nav,
        borrow_rate=config.borrow_rate,
        trading_cost_bps=config.trading_cost_bps,
        weight_drift=config.weight_drift,
    )
    rebals_df = _apply_realized_trades(rebals_df, traded_by_date, config.trading_cost_bps)
    diagnostics, diag_summary = _build_diagnostics(
        rebals_df,
        rebal_events + hold_events,
        costs_df,
        n_days_holdings=n_days_holdings,
        n_days_renorm=n_days_renorm,
    )

    ref_start = config.start_date - pd.Timedelta(days=15)
    rf_index = (
        load_reference_series(config.risk_free_series, ref_start, config.end_date)
        if config.risk_free_series
        else None
    )
    bench_index = (
        load_reference_series(config.benchmark_series, ref_start, config.end_date)
        if config.benchmark_series
        else None
    )
    subtract_rf = config.construction == "long_only"
    summary = build_summary(
        nav.dropna(),
        risk_free_rate=config.risk_free_rate,
        risk_free_index=rf_index,
        benchmark_index=bench_index,
        subtract_risk_free=subtract_rf,
    )
    summary.update(
        {
            "construction": config.construction,
            "style": config.style,
            "components": list(components),
            "component_max_age_days": dict(max_age_days),
            "n_rebals": int(len(weights_df)),
            "selection_mode": config.selection_mode,
            "quantile": config.quantile,
            "top_n": config.top_n,
            "adtv_min": config.adtv_min,
            "denomination": config.denomination,
            "one_class_per_issuer": config.one_class_per_issuer,
            "trading_cost_bps": config.trading_cost_bps,
            "borrow_rate": config.borrow_rate,
            "weight_drift": config.weight_drift,
            "beta_neutral": config.beta_neutral,
            "universe_index": list(config.universe_index) if config.universe_index else None,
            "min_component_coverage": config.min_component_coverage,
            "risk_free_series": config.risk_free_series,
            "benchmark_series": config.benchmark_series,
        }
    )
    summary.update(diag_summary)
    n_obs = int(summary.get("n_obs") or 0)
    summary.update(summarize_costs(costs_df, n_obs=n_obs))
    gross_nav = _gross_nav_from_net(
        strategy_returns, costs_df, initial_nav=config.initial_nav
    )
    if not gross_nav.empty:
        summary["gross_annualized_return"] = build_summary(gross_nav.dropna())[
            "annualized_return"
        ]
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
        prices=prices.loc[: config.end_date],
        component_scores=components_df,
    )
