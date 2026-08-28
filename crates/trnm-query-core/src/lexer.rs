use trnm_contracts::{DomainError, RetryClass, StableCode};

use crate::{error, QueryLimits};

const RESERVED_CHARS: &str = "+-=&|><!(){}[]^\"~*?:\\/ ";

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum Token {
    String(String),
    Phrase(String),
    Number(String),
    Plus,
    Minus,
    Colon,
    Greater,
    Less,
    Equal,
    Tilde(String),
    Boost(String),
}

pub(crate) fn lex(input: &str, limits: QueryLimits) -> Result<Vec<Token>, DomainError> {
    let characters: Vec<char> = input.chars().collect();
    let mut tokens = Vec::new();
    let mut index = 0;

    while index < characters.len() {
        let character = characters[index];
        if character.is_whitespace() {
            index += 1;
            continue;
        }

        let (token, next_index) = match character {
            '"' => {
                let (value, next) = read_phrase(&characters, index + 1, limits)?;
                (Token::Phrase(value), next)
            }
            '+' => (Token::Plus, index + 1),
            '-' => (Token::Minus, index + 1),
            ':' => (Token::Colon, index + 1),
            '>' => (Token::Greater, index + 1),
            '<' => (Token::Less, index + 1),
            '=' => (Token::Equal, index + 1),
            '^' => {
                let (value, next) = read_suffix(&characters, index + 1, limits)?;
                (Token::Boost(value), next)
            }
            '~' => {
                let (value, next) = read_suffix(&characters, index + 1, limits)?;
                (Token::Tilde(value), next)
            }
            '\\' => {
                let Some(escaped) = characters.get(index + 1).copied() else {
                    return Err(error(
                        StableCode::InvalidArgument,
                        "dangling_query_escape",
                        RetryClass::Never,
                    ));
                };
                let mut value = unescape(escaped);
                let next = read_string_continuation(&characters, index + 2, &mut value, limits)?;
                (Token::String(value), next)
            }
            value if value.is_ascii_digit() => {
                let (value, numeric, next) = read_word(&characters, index, true, limits)?;
                if numeric {
                    (Token::Number(value), next)
                } else {
                    (Token::String(value), next)
                }
            }
            _ => {
                let (value, _, next) = read_word(&characters, index, false, limits)?;
                (Token::String(value), next)
            }
        };

        tokens.push(token);
        if tokens.len() > limits.max_tokens {
            return Err(error(
                StableCode::ResourceExhausted,
                "query_token_limit_exceeded",
                RetryClass::Never,
            ));
        }
        index = next_index;
    }

    Ok(tokens)
}

fn read_phrase(
    characters: &[char],
    mut index: usize,
    limits: QueryLimits,
) -> Result<(String, usize), DomainError> {
    let mut value = String::new();
    let mut escaped = false;
    while let Some(character) = characters.get(index).copied() {
        if escaped {
            push_checked(&mut value, &unescape(character), limits)?;
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else if character == '"' {
            return Ok((value, index + 1));
        } else {
            push_char_checked(&mut value, character, limits)?;
        }
        index += 1;
    }
    Err(error(
        StableCode::InvalidArgument,
        "unterminated_query_quote",
        RetryClass::Never,
    ))
}

fn read_suffix(
    characters: &[char],
    mut index: usize,
    limits: QueryLimits,
) -> Result<(String, usize), DomainError> {
    let mut value = String::new();
    while let Some(character) = characters.get(index).copied() {
        if character == ' ' {
            return Ok((default_suffix(value), index + 1));
        }
        if character == '\\' {
            if let Some(escaped) = characters.get(index + 1).copied() {
                push_checked(&mut value, &unescape(escaped), limits)?;
                index += 2;
                continue;
            }
            return Ok((default_suffix(value), characters.len()));
        }
        push_char_checked(&mut value, character, limits)?;
        index += 1;
    }
    Ok((default_suffix(value), index))
}

fn default_suffix(value: String) -> String {
    if value.is_empty() {
        "1".to_owned()
    } else {
        value
    }
}

fn read_word(
    characters: &[char],
    index: usize,
    numeric_start: bool,
    limits: QueryLimits,
) -> Result<(String, bool, usize), DomainError> {
    let mut value = String::new();
    let mut numeric = numeric_start;
    let mut seen_dot = false;
    let mut cursor = index;

    while let Some(character) = characters.get(cursor).copied() {
        if character == ' ' || matches!(character, ':' | '^' | '~') {
            break;
        }
        if character == '\\' {
            if let Some(escaped) = characters.get(cursor + 1).copied() {
                push_checked(&mut value, &unescape(escaped), limits)?;
                numeric = false;
                cursor += 2;
                continue;
            }
            break;
        }
        if numeric {
            if character.is_ascii_digit() {
                push_char_checked(&mut value, character, limits)?;
            } else if character == '.' && !seen_dot {
                seen_dot = true;
                push_char_checked(&mut value, character, limits)?;
            } else {
                numeric = false;
                push_char_checked(&mut value, character, limits)?;
            }
        } else {
            push_char_checked(&mut value, character, limits)?;
        }
        cursor += 1;
    }

    Ok((value, numeric, cursor))
}

fn read_string_continuation(
    characters: &[char],
    mut index: usize,
    value: &mut String,
    limits: QueryLimits,
) -> Result<usize, DomainError> {
    while let Some(character) = characters.get(index).copied() {
        if character == ' ' || matches!(character, ':' | '^' | '~') {
            break;
        }
        if character == '\\' {
            if let Some(escaped) = characters.get(index + 1).copied() {
                push_checked(value, &unescape(escaped), limits)?;
                index += 2;
                continue;
            }
            break;
        }
        push_char_checked(value, character, limits)?;
        index += 1;
    }
    Ok(index)
}

fn push_checked(
    destination: &mut String,
    value: &str,
    limits: QueryLimits,
) -> Result<(), DomainError> {
    if destination.len().saturating_add(value.len()) > limits.max_token_bytes {
        return Err(error(
            StableCode::ResourceExhausted,
            "query_token_too_large",
            RetryClass::Never,
        ));
    }
    destination.push_str(value);
    Ok(())
}

fn push_char_checked(
    destination: &mut String,
    value: char,
    limits: QueryLimits,
) -> Result<(), DomainError> {
    if destination.len().saturating_add(value.len_utf8()) > limits.max_token_bytes {
        return Err(error(
            StableCode::ResourceExhausted,
            "query_token_too_large",
            RetryClass::Never,
        ));
    }
    destination.push(value);
    Ok(())
}

fn unescape(character: char) -> String {
    if RESERVED_CHARS.contains(character) {
        character.to_string()
    } else {
        format!("\\{character}")
    }
}
