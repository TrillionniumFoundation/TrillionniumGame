use crate::{parse_query, Clause, Comparison, Expression, Occur, Query, QueryLimits, TermKind};

fn parse(input: &str) -> Query {
    parse_query(input, QueryLimits::default()).unwrap()
}

fn only_clause(input: &str) -> Clause {
    match parse(input) {
        Query::Boolean(mut clauses) => {
            assert_eq!(clauses.len(), 1);
            clauses.remove(0)
        }
        Query::MatchAll | Query::MatchNone => panic!("expected boolean query"),
    }
}

#[test]
fn empty_and_star_match_nakama_wrapper_special_cases() {
    assert_eq!(parse(""), Query::MatchNone);
    assert_eq!(parse("*"), Query::MatchAll);
}

#[test]
fn fielded_terms_phrases_and_occurrence_are_parsed() {
    let query = parse("+region:ca -mode:ranked title:\"hello world\"");
    let Query::Boolean(clauses) = query else {
        panic!("expected boolean query");
    };
    assert_eq!(clauses.len(), 3);
    assert_eq!(clauses[0].occur, Occur::Must);
    assert_eq!(clauses[1].occur, Occur::MustNot);
    assert!(matches!(clauses[2].expression, Expression::Phrase { .. }));
}

#[test]
fn numeric_exact_represents_match_and_numeric_equality_semantics() {
    assert_eq!(
        only_clause("skill:33").expression,
        Expression::NumberExact {
            field: Some("skill".to_owned()),
            value: "33".to_owned(),
        }
    );
}

#[test]
fn negative_numeric_values_are_allowed_after_a_field_colon() {
    assert_eq!(
        only_clause("skill:-5").expression,
        Expression::NumberExact {
            field: Some("skill".to_owned()),
            value: "-5".to_owned(),
        }
    );
}

#[test]
fn numeric_and_date_ranges_preserve_inclusive_boundaries() {
    assert!(matches!(
        only_clause("skill:>=-5").expression,
        Expression::NumericRange {
            comparison: Comparison::GreaterThanOrEqual,
            ..
        }
    ));
    assert!(matches!(
        only_clause("created:<\"2006-01-02T15:04:05Z\"").expression,
        Expression::DateRange {
            comparison: Comparison::LessThan,
            ..
        }
    ));
}

#[test]
fn wildcard_regexp_fuzzy_and_boost_are_distinct() {
    assert!(matches!(
        only_clause("name:mart*").expression,
        Expression::Term {
            kind: TermKind::Wildcard,
            ..
        }
    ));
    assert!(matches!(
        only_clause("name:/mar.*ty/").expression,
        Expression::Term {
            kind: TermKind::Regexp,
            ..
        }
    ));
    let fuzzy = only_clause("name:watex~2 ^3");
    assert!(matches!(fuzzy.expression, Expression::Fuzzy { .. }));
    assert_eq!(fuzzy.boost.as_deref(), Some("3"));
}

#[test]
fn escaped_colon_space_and_leading_prefix_are_terms() {
    assert_eq!(
        only_clause("name\\:marty").expression,
        Expression::Term {
            field: None,
            value: "name:marty".to_owned(),
            kind: TermKind::Match,
        }
    );
    assert_eq!(
        only_clause("marty\\ couchbase").expression,
        Expression::Term {
            field: None,
            value: "marty couchbase".to_owned(),
            kind: TermKind::Match,
        }
    );
    assert_eq!(only_clause("\\+marty").occur, Occur::Should);
}

#[test]
fn plus_minus_and_comparison_characters_inside_terms_are_not_operators() {
    for input in [
        "field:t-est",
        "field:t+est",
        "field:t>est",
        "field:t<est",
        "field:t=est",
    ] {
        assert!(matches!(
            only_clause(input).expression,
            Expression::Term { .. }
        ));
    }
}

#[test]
fn tilde_without_value_defaults_to_one_and_space_starts_next_clause() {
    let Query::Boolean(clauses) = parse("watex~ 2") else {
        panic!("expected boolean query");
    };
    assert_eq!(clauses.len(), 2);
    assert!(matches!(
        &clauses[0].expression,
        Expression::Fuzzy { fuzziness, .. } if fuzziness == "1"
    ));
}

#[test]
fn invalid_quotes_dates_suffixes_and_field_gaps_fail_closed() {
    for input in [
        "\"unterminated",
        "field:",
        "field:>test",
        "test^bad",
        "name:watex~bad",
        "created:>\"2025-02-30T00:00:00Z\"",
    ] {
        assert!(
            parse_query(input, QueryLimits::default()).is_err(),
            "{input}"
        );
    }
}

#[test]
fn single_slash_is_rejected_instead_of_panicking() {
    assert_eq!(
        parse_query("/", QueryLimits::default())
            .unwrap_err()
            .reason(),
        "invalid_query_regexp"
    );
}

#[test]
fn query_token_and_clause_limits_are_enforced() {
    let limits = QueryLimits {
        max_query_bytes: 64,
        max_token_bytes: 4,
        max_tokens: 8,
        max_clauses: 2,
    };
    assert_eq!(
        parse_query("abcde", limits).unwrap_err().reason(),
        "query_token_too_large"
    );
    assert_eq!(
        parse_query("a b c", limits).unwrap_err().reason(),
        "query_clause_limit_exceeded"
    );
}

#[test]
fn non_reserved_escapes_preserve_the_backslash() {
    assert_eq!(
        only_clause("a\\zb").expression,
        Expression::Term {
            field: None,
            value: "a\\zb".to_owned(),
            kind: TermKind::Match,
        }
    );
}

#[test]
fn rfc3339_leap_year_and_offsets_are_validated() {
    assert!(parse_query(
        "created:>=\"2024-02-29T23:59:59.123+05:30\"",
        QueryLimits::default()
    )
    .is_ok());
    assert!(parse_query("created:>=\"2023-02-29T23:59:59Z\"", QueryLimits::default()).is_err());
}

#[test]
fn an_ip_address_transitions_from_numeric_to_term_after_second_dot() {
    assert_eq!(
        only_clause("127.0.0.1").expression,
        Expression::Term {
            field: None,
            value: "127.0.0.1".to_owned(),
            kind: TermKind::Match,
        }
    );
}
