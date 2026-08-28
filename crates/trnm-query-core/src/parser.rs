use trnm_contracts::{DomainError, RetryClass, StableCode};

use crate::lexer::Token;
use crate::{
    error, syntax_error, Clause, Comparison, Expression, Occur, Query, QueryLimits, TermKind,
};

#[derive(Debug)]
struct Parser {
    tokens: Vec<Token>,
    index: usize,
    limits: QueryLimits,
}

pub(crate) fn parse(tokens: Vec<Token>, limits: QueryLimits) -> Result<Query, DomainError> {
    Parser::new(tokens, limits).parse()
}

impl Parser {
    fn new(tokens: Vec<Token>, limits: QueryLimits) -> Self {
        Self {
            tokens,
            index: 0,
            limits,
        }
    }

    fn parse(mut self) -> Result<Query, DomainError> {
        let mut clauses = Vec::new();
        while self.peek().is_some() {
            if clauses.len() >= self.limits.max_clauses {
                return Err(error(
                    StableCode::ResourceExhausted,
                    "query_clause_limit_exceeded",
                    RetryClass::Never,
                ));
            }
            clauses.push(self.parse_clause()?);
        }
        Ok(Query::Boolean(clauses))
    }

    fn parse_clause(&mut self) -> Result<Clause, DomainError> {
        let occur = match self.peek() {
            Some(Token::Plus) => {
                self.index += 1;
                Occur::Must
            }
            Some(Token::Minus) => {
                self.index += 1;
                Occur::MustNot
            }
            _ => Occur::Should,
        };
        let expression = self.parse_expression()?;
        let boost = match self.peek() {
            Some(Token::Boost(value)) => {
                validate_float(value, "invalid_query_boost")?;
                let value = value.clone();
                self.index += 1;
                Some(value)
            }
            _ => None,
        };
        Ok(Clause {
            occur,
            expression,
            boost,
        })
    }

    fn parse_expression(&mut self) -> Result<Expression, DomainError> {
        match self.take().ok_or_else(syntax_error)? {
            Token::String(value) => {
                if matches!(self.peek(), Some(Token::Colon)) {
                    self.index += 1;
                    self.parse_field_expression(value)
                } else if let Some(Token::Tilde(fuzziness)) = self.peek() {
                    validate_float(fuzziness, "invalid_query_fuzziness")?;
                    let fuzziness = fuzziness.clone();
                    self.index += 1;
                    Ok(Expression::Fuzzy {
                        field: None,
                        value,
                        fuzziness,
                    })
                } else {
                    term(None, value)
                }
            }
            Token::Number(value) => {
                validate_float(&value, "invalid_query_number")?;
                Ok(Expression::NumberExact { field: None, value })
            }
            Token::Phrase(value) => Ok(Expression::Phrase { field: None, value }),
            _ => Err(syntax_error()),
        }
    }

    fn parse_field_expression(&mut self, field: String) -> Result<Expression, DomainError> {
        match self.take().ok_or_else(syntax_error)? {
            Token::String(value) => {
                if let Some(Token::Tilde(fuzziness)) = self.peek() {
                    validate_float(fuzziness, "invalid_query_fuzziness")?;
                    let fuzziness = fuzziness.clone();
                    self.index += 1;
                    Ok(Expression::Fuzzy {
                        field: Some(field),
                        value,
                        fuzziness,
                    })
                } else {
                    term(Some(field), value)
                }
            }
            Token::Number(value) => {
                validate_float(&value, "invalid_query_number")?;
                Ok(Expression::NumberExact {
                    field: Some(field),
                    value,
                })
            }
            Token::Minus => match self.take() {
                Some(Token::Number(value)) => {
                    let value = format!("-{value}");
                    validate_float(&value, "invalid_query_number")?;
                    Ok(Expression::NumberExact {
                        field: Some(field),
                        value,
                    })
                }
                _ => Err(syntax_error()),
            },
            Token::Phrase(value) => Ok(Expression::Phrase {
                field: Some(field),
                value,
            }),
            Token::Greater => self.parse_range(field, true),
            Token::Less => self.parse_range(field, false),
            _ => Err(syntax_error()),
        }
    }

    fn parse_range(&mut self, field: String, greater: bool) -> Result<Expression, DomainError> {
        let inclusive = matches!(self.peek(), Some(Token::Equal));
        if inclusive {
            self.index += 1;
        }
        let comparison = match (greater, inclusive) {
            (true, false) => Comparison::GreaterThan,
            (true, true) => Comparison::GreaterThanOrEqual,
            (false, false) => Comparison::LessThan,
            (false, true) => Comparison::LessThanOrEqual,
        };

        match self.take().ok_or_else(syntax_error)? {
            Token::Number(value) => {
                validate_float(&value, "invalid_query_number")?;
                Ok(Expression::NumericRange {
                    field,
                    comparison,
                    value,
                })
            }
            Token::Minus => match self.take() {
                Some(Token::Number(value)) => {
                    let value = format!("-{value}");
                    validate_float(&value, "invalid_query_number")?;
                    Ok(Expression::NumericRange {
                        field,
                        comparison,
                        value,
                    })
                }
                _ => Err(syntax_error()),
            },
            Token::Phrase(value) => {
                validate_rfc3339(&value)?;
                Ok(Expression::DateRange {
                    field,
                    comparison,
                    value,
                })
            }
            _ => Err(syntax_error()),
        }
    }

    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.index)
    }

    fn take(&mut self) -> Option<Token> {
        let token = self.tokens.get(self.index).cloned();
        if token.is_some() {
            self.index += 1;
        }
        token
    }
}

fn term(field: Option<String>, value: String) -> Result<Expression, DomainError> {
    let kind = if value.starts_with('/') && value.ends_with('/') {
        if value.len() < 2 {
            return Err(error(
                StableCode::InvalidArgument,
                "invalid_query_regexp",
                RetryClass::Never,
            ));
        }
        TermKind::Regexp
    } else if value.contains('*') || value.contains('?') {
        TermKind::Wildcard
    } else {
        TermKind::Match
    };
    Ok(Expression::Term { field, value, kind })
}

fn validate_float(value: &str, reason: &'static str) -> Result<(), DomainError> {
    if value.parse::<f64>().is_err() {
        Err(error(
            StableCode::InvalidArgument,
            reason,
            RetryClass::Never,
        ))
    } else {
        Ok(())
    }
}

fn validate_rfc3339(value: &str) -> Result<(), DomainError> {
    let bytes = value.as_bytes();
    if bytes.len() < 20
        || bytes.get(4) != Some(&b'-')
        || bytes.get(7) != Some(&b'-')
        || bytes.get(10) != Some(&b'T')
        || bytes.get(13) != Some(&b':')
        || bytes.get(16) != Some(&b':')
    {
        return Err(invalid_date());
    }

    let year = decimal(bytes, 0, 4)?;
    let month = decimal(bytes, 5, 7)?;
    let day = decimal(bytes, 8, 10)?;
    let hour = decimal(bytes, 11, 13)?;
    let minute = decimal(bytes, 14, 16)?;
    let second = decimal(bytes, 17, 19)?;
    if !(1..=12).contains(&month)
        || !(0..=23).contains(&hour)
        || !(0..=59).contains(&minute)
        || !(0..=59).contains(&second)
        || day == 0
        || day > days_in_month(year, month)
    {
        return Err(invalid_date());
    }

    let mut index = 19;
    if bytes.get(index) == Some(&b'.') {
        index += 1;
        let fractional_start = index;
        while bytes
            .get(index)
            .is_some_and(|byte| byte.is_ascii_digit())
        {
            index += 1;
        }
        if index == fractional_start {
            return Err(invalid_date());
        }
    }

    match bytes.get(index) {
        Some(&b'Z') if index + 1 == bytes.len() => Ok(()),
        Some(&b'+') | Some(&b'-') if index + 6 == bytes.len() => {
            if bytes.get(index + 3) != Some(&b':') {
                return Err(invalid_date());
            }
            let offset_hour = decimal(bytes, index + 1, index + 3)?;
            let offset_minute = decimal(bytes, index + 4, index + 6)?;
            if (0..=23).contains(&offset_hour) && (0..=59).contains(&offset_minute) {
                Ok(())
            } else {
                Err(invalid_date())
            }
        }
        _ => Err(invalid_date()),
    }
}

fn decimal(bytes: &[u8], start: usize, end: usize) -> Result<u32, DomainError> {
    let Some(slice) = bytes.get(start..end) else {
        return Err(invalid_date());
    };
    if !slice.iter().all(|byte| byte.is_ascii_digit()) {
        return Err(invalid_date());
    }
    let mut value = 0_u32;
    for digit in slice {
        value = value * 10 + u32::from(*digit - b'0');
    }
    Ok(value)
}

const fn days_in_month(year: u32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if is_leap_year(year) => 29,
        2 => 28,
        _ => 0,
    }
}

const fn is_leap_year(year: u32) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

const fn invalid_date() -> DomainError {
    error(
        StableCode::InvalidArgument,
        "invalid_query_date",
        RetryClass::Never,
    )
}
