-- Design draft, 2026-09-28. Run only against an empty isolated database.
-- Connection policy (WAL/FULL/busy_timeout) belongs to the runtime, not this DDL.
-- Dates, finite numbers, units and source precedence also require writer validation.
PRAGMA user_version = 1;

CREATE TABLE daily_bars (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL CHECK(length(trade_date) = 10),
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    turnover_rate REAL, vol_ratio REAL, pct_chg REAL, amplitude REAL,
    total_share REAL, float_share REAL, float_shares REAL,
    total_mv REAL, float_mv REAL, pe_ttm REAL, pb REAL,
    name TEXT, name_as_of TEXT, name_source TEXT, ohlcv_source TEXT,
    turnover_rate_source TEXT, vol_ratio_source TEXT,
    pct_chg_source TEXT, amplitude_source TEXT,
    total_share_source TEXT, float_share_source TEXT,
    total_mv_source TEXT, float_mv_source TEXT,
    updated_at INTEGER NOT NULL CHECK(updated_at > 0),
    PRIMARY KEY(symbol, trade_date)
) STRICT, WITHOUT ROWID;
CREATE INDEX daily_bars_by_date ON daily_bars(trade_date, symbol);

CREATE TABLE corporate_actions (
    symbol TEXT NOT NULL,
    effective_date TEXT NOT NULL CHECK(length(effective_date) = 10),
    record_kind TEXT NOT NULL CHECK(record_kind IN ('event', 'factor_anchor', 'factor')),
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    category INTEGER,
    payload_json TEXT CHECK(payload_json IS NULL OR json_valid(payload_json)),
    source_cumulative_factor REAL CHECK(source_cumulative_factor > 0),
    event_factor REAL CHECK(event_factor > 0),
    cumulative_factor REAL CHECK(cumulative_factor > 0),
    valid_from TEXT,
    valid_through TEXT,
    factor_basis TEXT,
    updated_at INTEGER NOT NULL CHECK(updated_at > 0),
    PRIMARY KEY(symbol, effective_date, record_kind, source, source_key),
    CHECK(record_kind <> 'event' OR (category IS NOT NULL AND payload_json IS NOT NULL)),
    CHECK(record_kind <> 'factor_anchor' OR source_cumulative_factor IS NOT NULL),
    CHECK(record_kind <> 'factor' OR
        (cumulative_factor IS NOT NULL AND valid_from IS NOT NULL
         AND valid_through IS NOT NULL AND factor_basis IS NOT NULL
         AND valid_from = effective_date AND effective_date <= valid_through)),
    CHECK(record_kind = 'factor' OR (event_factor IS NULL AND cumulative_factor IS NULL)),
    CHECK(record_kind = 'factor_anchor' OR source_cumulative_factor IS NULL)
) STRICT, WITHOUT ROWID;
-- Exactly one selected factor per symbol/date, even when multiple sources exist.
CREATE UNIQUE INDEX corporate_actions_selected_factor
    ON corporate_actions(symbol, effective_date) WHERE record_kind = 'factor';

CREATE TABLE daily_features (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL CHECK(length(trade_date) = 10),
    -- Source candidates are independent of the effective computed values.
    source_pre_close REAL, source_pre_close_source TEXT,
    source_is_st INTEGER CHECK(source_is_st IN (0, 1)),
    source_is_st_source TEXT,
    is_st_name_date TEXT,
    trading_status TEXT, trading_status_source TEXT,
    pre_close REAL, pre_close_source TEXT,
    is_st INTEGER CHECK(is_st IN (0, 1)), is_st_source TEXT,
    calc_status TEXT NOT NULL CHECK(calc_status IN
        ('TRADED', 'NO_TRADE', 'INVALID')),
    limit_status TEXT CHECK(limit_status IN ('KNOWN', 'NO_LIMIT', 'UNKNOWN', 'INVALID')),
    limit_reason TEXT,
    limit_up_price REAL, limit_down_price REAL,
    touch_limit_up INTEGER CHECK(touch_limit_up IN (0, 1)),
    close_limit_up INTEGER CHECK(close_limit_up IN (0, 1)),
    touch_limit_down INTEGER CHECK(touch_limit_down IN (0, 1)),
    close_limit_down INTEGER CHECK(close_limit_down IN (0, 1)),
    consecutive_up INTEGER CHECK(consecutive_up >= 0),
    prior_consecutive_up INTEGER CHECK(prior_consecutive_up >= 0),
    streak_known INTEGER CHECK(streak_known IN (0, 1)),
    ma20 REAL CHECK(ma20 > 0),
    above_ma20 INTEGER CHECK(above_ma20 IN (0, 1)),
    updated_at INTEGER NOT NULL CHECK(updated_at > 0),
    PRIMARY KEY(symbol, trade_date),
    CHECK((ma20 IS NULL) = (above_ma20 IS NULL)),
    CHECK(NOT (close_limit_up IS 1 AND close_limit_down IS 1)),
    CHECK(close_limit_up IS NOT 1 OR touch_limit_up IS 1),
    CHECK(close_limit_down IS NOT 1 OR touch_limit_down IS 1),
    CHECK(streak_known IS NOT 1 OR consecutive_up IS NOT NULL),
    CHECK(streak_known IS NOT 0 OR consecutive_up IS NULL),
    CHECK(calc_status <> 'INVALID' OR
        (limit_status IS 'INVALID' AND streak_known IS 0
         AND consecutive_up IS NULL AND ma20 IS NULL AND above_ma20 IS NULL)),
    CHECK(limit_status NOT IN ('UNKNOWN', 'INVALID') OR
        (touch_limit_up IS NULL AND close_limit_up IS NULL
         AND touch_limit_down IS NULL AND close_limit_down IS NULL
         AND limit_up_price IS NULL AND limit_down_price IS NULL)),
    CHECK(limit_status <> 'NO_LIMIT' OR
        (touch_limit_up IS 0 AND close_limit_up IS 0
         AND touch_limit_down IS 0 AND close_limit_down IS 0
         AND consecutive_up IS 0 AND streak_known IS 1)),
    CHECK(calc_status <> 'NO_TRADE' OR
        (limit_status IS NULL AND limit_up_price IS NULL AND limit_down_price IS NULL
         AND touch_limit_up IS NULL AND close_limit_up IS NULL
         AND touch_limit_down IS NULL AND close_limit_down IS NULL
         AND consecutive_up IS NULL AND prior_consecutive_up IS NULL
         AND streak_known IS NULL AND ma20 IS NULL AND above_ma20 IS NULL)),
    CHECK(limit_status <> 'KNOWN' OR
        (pre_close IS NOT NULL AND pre_close > 0
         AND limit_up_price IS NOT NULL AND limit_up_price > 0
         AND limit_down_price IS NOT NULL AND limit_down_price > 0
         AND touch_limit_up IS NOT NULL AND close_limit_up IS NOT NULL
         AND touch_limit_down IS NOT NULL AND close_limit_down IS NOT NULL))
) STRICT, WITHOUT ROWID;
-- No foreign key to bars: dated source facts without a bar must be retained.
CREATE INDEX daily_features_by_date ON daily_features(trade_date, symbol);
CREATE INDEX daily_features_events ON daily_features(trade_date, symbol)
    WHERE close_limit_up = 1 OR touch_limit_up = 1
       OR close_limit_down = 1 OR touch_limit_down = 1;

CREATE TABLE market_daily_summary (
    frequency TEXT NOT NULL CHECK(frequency IN ('D', 'W', 'M')),
    period_key TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('all_stocks', 'exclude_known_st')),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    as_of TEXT NOT NULL,
    session_count INTEGER NOT NULL CHECK(session_count >= 0),
    -- D-only fields; W/M rows use the explicitly named aggregates below.
    trading_count INTEGER, st_unknown_count INTEGER,
    valid_return_count INTEGER, invalid_return_count INTEGER,
    up_count INTEGER, down_count INTEGER, flat_count INTEGER,
    strong_up_count INTEGER, strong_down_count INTEGER,
    avg_return REAL, median_return REAL,
    return_distribution_json TEXT CHECK(return_distribution_json IS NULL OR
        (json_valid(return_distribution_json) AND json_type(return_distribution_json) = 'object')),
    up_pct REAL, strong_up_pct REAL, strong_down_pct REAL,
    close_limit_up_count INTEGER, touch_limit_up_count INTEGER,
    close_limit_down_count INTEGER, touch_limit_down_count INTEGER,
    limit_known_count INTEGER, no_limit_count INTEGER,
    limit_unknown_count INTEGER, limit_invalid_count INTEGER,
    streak_unknown_count INTEGER, max_consecutive_up INTEGER,
    first_board_count INTEGER, second_plus_count INTEGER,
    third_plus_count INTEGER, fifth_plus_count INTEGER,
    ladder_json TEXT CHECK(ladder_json IS NULL OR json_valid(ladder_json)),
    promotion_eligible_count INTEGER, promotion_success_count INTEGER,
    -- NULL means this optional quality extension has not been calculated.
    limit_reason_counts_json TEXT CHECK(limit_reason_counts_json IS NULL OR
        (json_valid(limit_reason_counts_json) AND json_type(limit_reason_counts_json)='object')),
    promotion_quality_json TEXT CHECK(promotion_quality_json IS NULL OR
        (json_valid(promotion_quality_json) AND json_type(promotion_quality_json)='object')),
    -- Common fields: D values or W/M aggregates, with named valid denominators.
    amount_sum REAL, amount_valid_count INTEGER,
    avg_turnover REAL, turnover_valid_count INTEGER,
    sealed_ratio REAL, promotion_ratio REAL,
    ma20_valid_count INTEGER, above_ma20_count INTEGER, above_ma20_pct REAL,
    -- W/M-only daily-feature aggregates and native period observations.
    trading_observations INTEGER, st_unknown_observations INTEGER,
    valid_return_observations INTEGER,
    daily_up_pct_weighted REAL, daily_strong_up_pct_weighted REAL,
    daily_strong_down_pct_weighted REAL, daily_avg_return_weighted REAL,
    daily_median_return_mean REAL, daily_median_valid_sessions INTEGER,
    daily_return_distribution_json TEXT CHECK(daily_return_distribution_json IS NULL OR
        (json_valid(daily_return_distribution_json) AND json_type(daily_return_distribution_json) = 'object')),
    period_return_distribution_json TEXT CHECK(period_return_distribution_json IS NULL OR
        (json_valid(period_return_distribution_json) AND json_type(period_return_distribution_json) = 'object')),
    daily_limit_up_mean REAL,
    limit_up_occurrences INTEGER, limit_up_symbols INTEGER,
    limit_touch_up_occurrences INTEGER,
    limit_down_occurrences INTEGER, limit_touch_down_occurrences INTEGER,
    limit_unknown_observations INTEGER, limit_invalid_observations INTEGER,
    streak_unknown_observations INTEGER,
    end_max_consecutive_up INTEGER, period_max_consecutive_up INTEGER,
    promotion_eligible_observations INTEGER, promotion_success_observations INTEGER,
    period_valid_return_count INTEGER, period_unknown_return_count INTEGER,
    period_up_count INTEGER, period_down_count INTEGER, period_flat_count INTEGER,
    period_avg_return REAL, period_median_return REAL,
    updated_at INTEGER NOT NULL CHECK(updated_at > 0),
    PRIMARY KEY(frequency, period_key, scope),
    CHECK(period_start <= as_of AND as_of <= period_end)
) STRICT, WITHOUT ROWID;
CREATE INDEX market_summary_by_range
    ON market_daily_summary(frequency, scope, period_start);
