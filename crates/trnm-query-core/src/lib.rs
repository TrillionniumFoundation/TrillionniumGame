// SPDX-License-Identifier: Apache-2.0
//
// Original Rust implementation informed by the public grammar and tests of
// blugelabs/query_string v0.3.0. Exact source identities are recorded in
// contracts/query/upstream-query-lock.json.
#![forbid(unsafe_code)]

mod lexer;
mod parser;

#[cfg(test)]
mod tests;

use trnm_contracts::{DomainError, RetryClass, StableCode};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct QueryLimits {
    pub max_query_bytes: usize,
    pub max_token_bytes: usize,
    pub max_tokens: usize,
    pub max_clauses: usize,
}

impl Default for QueryLimits {
    fn default() -> Self {
        Self {
            max_query_bytes: 4_096,
            max_token_bytes: 1_024,
            max_tokens: 256,
            max_clauses: 64,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Query {
    MatchAll,
    MatchNone,
    Boolean(Vec<Clause>),
}

impl Query {
    #[must_use]
    pub fn clause_count(&self) -> usize {
        match self {
            Self::Boolean(clauses) => clauses.len(),
            Self::MatchAll | Self::MatchNone => 0,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Occur {
    Should,
    Must,
    MustNot,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Clause {
    pub occur: Occur,
    pub expression: Expression,
    pub boost: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TermKind {
    Match,
    Wildcard,
    Regexp,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Comparison {
    GreaterThan,
    GreaterThanOrEqual,
    LessThan,
    LessThanOrEqual,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Expression {
    Term {
        field: Option<String>,
        value: String,
        kind: TermKind,
    },
    Phrase {
        field: Option<String>,
        value: String,
    },
    NumberExact {
        field: Option<String>,
        value: String,
    },
    Fuzzy {
        field: Option<String>,
        value: String,
        fuzziness: String,
    },
    NumericRange {
        field: String,
        comparison: Comparison,
        value: String,
    },
    DateRange {
        field: String,
        comparison: Comparison,
        value: String,
    },
}

pub fn parse_query(input: &str, limits: QueryLimits) -> Result<Query, DomainError> {
    if input.len() > limits.max_query_bytes {
        return Err(error(
            StableCode::ResourceExhausted,
            "query_too_large",
            RetryClass::Never,
        ));
    }
    if input.is_empty() {
        return Ok(Query::MatchNone);
    }
    if input == "*" {
        return Ok(Query::MatchAll);
    }

    let tokens = lexer::lex(input, limits)?;
    if tokens.is_empty() {
        return Err(syntax_error());
    }
    parser::parse(tokens, limits)
}

pub(crate) const fn syntax_error() -> DomainError {
    error(
        StableCode::InvalidArgument,
        "invalid_query_syntax",
        RetryClass::Never,
    )
}

pub(crate) const fn error(
    code: StableCode,
    reason: &'static str,
    retry: RetryClass,
) -> DomainError {
    DomainError::new(code, reason, retry)
}
