//! Bounded decoder for the pinned Nakama storage cursor: Go gob followed by
//! unpadded URL base64. This is an untrusted page offset, never an ACL witness.
//!
//! The wire shape derives from Apache-2.0 Nakama d4d92f93. Gob semantics are
//! checked against Go 1.26.5 (BSD-3-Clause), UUID binary semantics against
//! gofrs/uuid 5.4.0 (MIT). This independently written codec is a source candidate.
//! Interfaces, reserved gob metadata value types, and noncanonical descriptors
//! with several wire kinds are outside this bounded subset. Stronger resource
//! limits than Go's decoder are deliberate, separately recorded differences.

use std::collections::{BTreeMap, BTreeSet};

use trnm_contracts::UserId;
use trnm_persistence_pg::StorageListPosition;

const MAX_ENCODED_BYTES: usize = 16 * 1024;
const MAX_DECODED_BYTES: usize = 12 * 1024;
const MAX_MESSAGES: usize = 64;
const MAX_TYPES: usize = 64;
const MAX_FIELDS: usize = 64;
const MAX_DEPTH: usize = 16;
const MAX_ITEMS: usize = 1024;
const MAX_KEY_BYTES: usize = 4096;
const BASE64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

#[derive(Clone, Debug)]
struct Field {
    name: String,
    id: i32,
}

#[derive(Clone, Debug)]
enum WireType {
    Array { element: i32, length: usize },
    Slice(i32),
    Struct(Vec<Field>),
    Map { key: i32, element: i32 },
    External(ExternalKind),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ExternalKind {
    Gob,
    Binary,
    Text,
}

/// Decode one gob value, as upstream does. A valid first value may be followed
/// by another message or arbitrary bytes. All bytes still count toward limits.
pub(super) fn decode_cursor(input: &str) -> Result<StorageListPosition, ()> {
    let bytes = decode_base64(input)?;
    let mut stream = Reader::new(&bytes);
    let mut types = BTreeMap::new();
    let mut items = MAX_ITEMS;
    for _ in 0..MAX_MESSAGES {
        let length = stream.length()?;
        let mut message = Reader::new(stream.take(length)?);
        let id = message.signed()?;
        if id < 0 {
            let id = i32::try_from(id.checked_neg().ok_or(())?).map_err(|_| ())?;
            if id < 64 || types.len() >= MAX_TYPES || types.contains_key(&id) {
                return Err(());
            }
            types.insert(id, read_wire_type(&mut message, &mut items)?);
            // Unlike the first value, a descriptor message cannot have a
            // suffix: Go reports an extra-data-in-buffer type-sequence error.
            if message.offset != message.bytes.len() {
                return Err(());
            }
        } else {
            let id = i32::try_from(id).map_err(|_| ())?;
            return read_position(&mut message, id, &types, &mut items);
        }
    }
    Err(())
}

/// Emit an ordinary gob storageCursor with arbitrary valid local type IDs.
/// Go's global allocation history can change its IDs; the receiver learns the
/// descriptors rather than relying on these particular numbers.
pub(super) fn encode_cursor(position: &StorageListPosition) -> Result<String, ()> {
    if position.key.len() > MAX_KEY_BYTES {
        return Err(());
    }
    let mut stream = Vec::new();
    let fields = [
        Field {
            name: "Key".into(),
            id: 6,
        },
        Field {
            name: "UserID".into(),
            id: 65,
        },
        Field {
            name: "Read".into(),
            id: 2,
        },
    ];
    write_struct_descriptor(&mut stream, 64, "storageCursor", &fields);
    write_external_descriptor(&mut stream, 65, "UUID", ExternalKind::Binary);
    let mut value = Vec::new();
    write_signed(&mut value, 64);
    let mut previous = None;
    if !position.key.is_empty() {
        write_field(&mut value, &mut previous, 0);
        write_bytes(&mut value, position.key.as_bytes());
    }
    if !position.user_id.is_zero() {
        write_field(&mut value, &mut previous, 1);
        write_bytes(&mut value, position.user_id.as_bytes());
    }
    if position.read != 0 {
        write_field(&mut value, &mut previous, 2);
        write_signed(&mut value, i64::from(position.read));
    }
    write_unsigned(&mut value, 0);
    write_message(&mut stream, &value);
    if stream.len() > MAX_DECODED_BYTES {
        return Err(());
    }
    Ok(encode_base64(&stream))
}

struct Reader<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl<'a> Reader<'a> {
    const fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, offset: 0 }
    }

    fn take(&mut self, length: usize) -> Result<&'a [u8], ()> {
        let end = self.offset.checked_add(length).ok_or(())?;
        let value = self.bytes.get(self.offset..end).ok_or(())?;
        self.offset = end;
        Ok(value)
    }

    fn unsigned(&mut self) -> Result<u64, ()> {
        let first = self.take(1)?[0];
        if first <= 127 {
            return Ok(u64::from(first));
        }
        let length = usize::from(256_u16 - u16::from(first));
        if length > 8 {
            return Err(());
        }
        let mut result = 0_u64;
        for byte in self.take(length)? {
            result = (result << 8) | u64::from(*byte);
        }
        Ok(result)
    }

    fn signed(&mut self) -> Result<i64, ()> {
        let unsigned = self.unsigned()?;
        let magnitude = i64::try_from(unsigned >> 1).map_err(|_| ())?;
        Ok(if unsigned & 1 == 0 {
            magnitude
        } else {
            !magnitude
        })
    }

    fn length(&mut self) -> Result<usize, ()> {
        usize::try_from(self.unsigned()?).map_err(|_| ())
    }

    fn byte_string(&mut self) -> Result<&'a [u8], ()> {
        let length = self.length()?;
        self.take(length)
    }

    fn string(&mut self, limit: usize) -> Result<String, ()> {
        let bytes = self.byte_string()?;
        if bytes.len() > limit {
            return Err(());
        }
        String::from_utf8(bytes.to_vec()).map_err(|_| ())
    }

    fn field(&mut self, previous: &mut Option<usize>) -> Result<Option<usize>, ()> {
        // Go's decodeStruct stops at an empty message buffer even when its
        // explicit zero terminator was omitted. Partial fields still fail.
        if self.offset == self.bytes.len() {
            return Ok(None);
        }
        let delta = self.length()?;
        if delta == 0 {
            return Ok(None);
        }
        let field = match *previous {
            Some(previous) => previous.checked_add(delta).ok_or(())?,
            None => delta - 1,
        };
        *previous = Some(field);
        Ok(Some(field))
    }
}

fn spend(items: &mut usize, amount: usize) -> Result<(), ()> {
    *items = items.checked_sub(amount).ok_or(())?;
    Ok(())
}

fn read_common(reader: &mut Reader<'_>) -> Result<(), ()> {
    let mut previous = None;
    while let Some(field) = reader.field(&mut previous)? {
        match field {
            0 => {
                reader.byte_string()?;
            }
            1 => {
                reader.signed()?;
            }
            _ => return Err(()),
        }
    }
    Ok(())
}

fn read_field_type(reader: &mut Reader<'_>) -> Result<Field, ()> {
    let mut field = Field {
        name: String::new(),
        id: 0,
    };
    let mut previous = None;
    while let Some(index) = reader.field(&mut previous)? {
        match index {
            0 => field.name = reader.string(MAX_KEY_BYTES)?,
            1 => field.id = i32::try_from(reader.signed()?).map_err(|_| ())?,
            _ => return Err(()),
        }
    }
    Ok(field)
}

fn read_wire_type(reader: &mut Reader<'_>, items: &mut usize) -> Result<WireType, ()> {
    let mut previous = None;
    let mut wire = None;
    while let Some(kind) = reader.field(&mut previous)? {
        if wire.is_some() {
            return Err(());
        }
        let mut nested_previous = None;
        let mut element = 0;
        let mut key = 0;
        let mut length = 0;
        let mut fields = Vec::new();
        while let Some(field) = reader.field(&mut nested_previous)? {
            match (kind, field) {
                (0..=6, 0) => read_common(reader)?,
                (0 | 1, 1) | (3, 2) => element = i32::try_from(reader.signed()?).map_err(|_| ())?,
                (0, 2) => length = usize::try_from(reader.signed()?).map_err(|_| ())?,
                (3, 1) => key = i32::try_from(reader.signed()?).map_err(|_| ())?,
                (2, 1) => {
                    let count = reader.length()?;
                    if count > MAX_FIELDS {
                        return Err(());
                    }
                    spend(items, count)?;
                    for _ in 0..count {
                        fields.push(read_field_type(reader)?);
                    }
                }
                _ => return Err(()),
            }
        }
        wire = Some(match kind {
            0 => WireType::Array { element, length },
            1 => WireType::Slice(element),
            2 => WireType::Struct(fields),
            3 => WireType::Map { key, element },
            4 => WireType::External(ExternalKind::Gob),
            5 => WireType::External(ExternalKind::Binary),
            6 => WireType::External(ExternalKind::Text),
            _ => return Err(()),
        });
    }
    wire.ok_or(())
}

fn read_position(
    reader: &mut Reader<'_>,
    id: i32,
    types: &BTreeMap<i32, WireType>,
    items: &mut usize,
) -> Result<StorageListPosition, ()> {
    let Some(WireType::Struct(fields)) = types.get(&id) else {
        return Err(());
    };
    let mut matched = false;
    // Go compiles compatibility for every descriptor field, including fields
    // whose zero values were omitted from the actual value.
    for field in fields {
        match field.name.as_str() {
            "Key" if field.id == 6 => matched = true,
            "Read" if field.id == 2 => matched = true,
            "UserID"
                if matches!(
                    types.get(&field.id),
                    Some(WireType::External(ExternalKind::Binary))
                ) =>
            {
                matched = true
            }
            "Key" | "Read" | "UserID" | "" => return Err(()),
            _ => validate_skip_type(field.id, types, &mut BTreeSet::new(), 0)?,
        }
    }
    if !matched && !fields.is_empty() {
        return Err(());
    }
    let mut result = StorageListPosition {
        key: String::new(),
        user_id: UserId::new([0; 16]),
        read: 0,
    };
    let mut previous = None;
    while let Some(index) = reader.field(&mut previous)? {
        let field = fields.get(index).ok_or(())?;
        spend(items, 1)?;
        match field.name.as_str() {
            "Key" => result.key = reader.string(MAX_KEY_BYTES)?,
            "Read" => result.read = i32::try_from(reader.signed()?).map_err(|_| ())?,
            "UserID" => {
                result.user_id = UserId::new(reader.byte_string()?.try_into().map_err(|_| ())?)
            }
            _ => skip_value(reader, field.id, types, items, 0)?,
        }
    }
    Ok(result)
}

fn validate_skip_type(
    id: i32,
    types: &BTreeMap<i32, WireType>,
    seen: &mut BTreeSet<i32>,
    depth: usize,
) -> Result<(), ()> {
    if depth >= MAX_DEPTH {
        return Err(());
    }
    if (1..=7).contains(&id) || seen.contains(&id) {
        return Ok(());
    }
    seen.insert(id);
    match types.get(&id).ok_or(())? {
        WireType::External(_) => Ok(()),
        WireType::Array { element, .. } | WireType::Slice(element) => {
            validate_skip_type(*element, types, seen, depth + 1)
        }
        WireType::Map { key, element } => {
            validate_skip_type(*key, types, seen, depth + 1)?;
            validate_skip_type(*element, types, seen, depth + 1)
        }
        WireType::Struct(fields) => {
            for field in fields {
                validate_skip_type(field.id, types, seen, depth + 1)?;
            }
            Ok(())
        }
    }
}

fn skip_value(
    reader: &mut Reader<'_>,
    id: i32,
    types: &BTreeMap<i32, WireType>,
    items: &mut usize,
    depth: usize,
) -> Result<(), ()> {
    if depth >= MAX_DEPTH {
        return Err(());
    }
    match id {
        1..=4 => {
            reader.unsigned()?;
        }
        5 | 6 => {
            reader.byte_string()?;
        }
        7 => {
            reader.unsigned()?;
            reader.unsigned()?;
        }
        _ => match types.get(&id).ok_or(())? {
            WireType::External(_) => {
                reader.byte_string()?;
            }
            WireType::Array { element, length } => {
                let count = reader.length()?;
                if count != *length {
                    return Err(());
                }
                skip_sequence(reader, *element, count, types, items, depth)?;
            }
            WireType::Slice(element) => {
                let count = reader.length()?;
                skip_sequence(reader, *element, count, types, items, depth)?;
            }
            WireType::Map { key, element } => {
                let count = reader.length()?;
                spend(items, count.checked_mul(2).ok_or(())?)?;
                for _ in 0..count {
                    skip_value(reader, *key, types, items, depth + 1)?;
                    skip_value(reader, *element, types, items, depth + 1)?;
                }
            }
            WireType::Struct(fields) => {
                let mut previous = None;
                while let Some(index) = reader.field(&mut previous)? {
                    let field = fields.get(index).ok_or(())?;
                    spend(items, 1)?;
                    skip_value(reader, field.id, types, items, depth + 1)?;
                }
            }
        },
    }
    Ok(())
}

fn skip_sequence(
    reader: &mut Reader<'_>,
    element: i32,
    count: usize,
    types: &BTreeMap<i32, WireType>,
    items: &mut usize,
    depth: usize,
) -> Result<(), ()> {
    spend(items, count)?;
    for _ in 0..count {
        skip_value(reader, element, types, items, depth + 1)?;
    }
    Ok(())
}

fn decode_base64(input: &str) -> Result<Vec<u8>, ()> {
    if input.len() > MAX_ENCODED_BYTES {
        return Err(());
    }
    let mut output = Vec::with_capacity(input.len() / 4 * 3);
    let mut value = 0_u32;
    let mut digits = 0_usize;
    for byte in input.bytes().filter(|byte| !matches!(byte, b'\r' | b'\n')) {
        let digit = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'-' => 62,
            b'_' => 63,
            _ => return Err(()),
        };
        value = (value << 6) | u32::from(digit);
        digits += 1;
        if digits == 4 {
            output.extend_from_slice(&value.to_be_bytes()[1..]);
            value = 0;
            digits = 0;
        }
    }
    match digits {
        0 => {}
        1 => return Err(()),
        2 => output.push((value >> 4) as u8),
        3 => {
            output.push((value >> 10) as u8);
            output.push((value >> 2) as u8);
        }
        _ => unreachable!("four digits are flushed"),
    }
    if output.len() > MAX_DECODED_BYTES {
        return Err(());
    }
    Ok(output)
}

fn encode_base64(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let word = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        for index in 0..chunk.len() + 1 {
            output.push(char::from(
                BASE64[((word >> (18 - index * 6)) & 63) as usize],
            ));
        }
    }
    output
}

fn write_unsigned(output: &mut Vec<u8>, value: u64) {
    if value <= 127 {
        output.push(value as u8);
        return;
    }
    let bytes = value.to_be_bytes();
    let leading = bytes.iter().take_while(|byte| **byte == 0).count();
    let length = 8 - leading;
    output.push((256 - length) as u8);
    output.extend_from_slice(&bytes[leading..]);
}

fn write_signed(output: &mut Vec<u8>, value: i64) {
    let magnitude = if value < 0 {
        ((!value as u64) << 1) | 1
    } else {
        (value as u64) << 1
    };
    write_unsigned(output, magnitude);
}

fn write_bytes(output: &mut Vec<u8>, bytes: &[u8]) {
    write_unsigned(output, bytes.len() as u64);
    output.extend_from_slice(bytes);
}

fn write_field(output: &mut Vec<u8>, previous: &mut Option<usize>, field: usize) {
    write_unsigned(
        output,
        previous.map_or(field + 1, |previous| field - previous) as u64,
    );
    *previous = Some(field);
}

fn write_message(output: &mut Vec<u8>, message: &[u8]) {
    write_bytes(output, message);
}

fn write_common(output: &mut Vec<u8>, id: i32, name: &str) {
    write_unsigned(output, 1);
    write_bytes(output, name.as_bytes());
    write_unsigned(output, 1);
    write_signed(output, i64::from(id));
    write_unsigned(output, 0);
}

fn write_struct_descriptor(output: &mut Vec<u8>, id: i32, name: &str, fields: &[Field]) {
    let mut message = Vec::new();
    write_signed(&mut message, -i64::from(id));
    write_unsigned(&mut message, 3); // wireType.StructT
    write_unsigned(&mut message, 1); // structType.CommonType
    write_common(&mut message, id, name);
    write_unsigned(&mut message, 1); // structType.Field
    write_unsigned(&mut message, fields.len() as u64);
    for field in fields {
        write_unsigned(&mut message, 1);
        write_bytes(&mut message, field.name.as_bytes());
        write_unsigned(&mut message, 1);
        write_signed(&mut message, i64::from(field.id));
        write_unsigned(&mut message, 0);
    }
    write_unsigned(&mut message, 0);
    write_unsigned(&mut message, 0);
    write_message(output, &message);
}

fn write_external_descriptor(output: &mut Vec<u8>, id: i32, name: &str, kind: ExternalKind) {
    let mut message = Vec::new();
    write_signed(&mut message, -i64::from(id));
    write_unsigned(
        &mut message,
        match kind {
            ExternalKind::Gob => 5,
            ExternalKind::Binary => 6,
            ExternalKind::Text => 7,
        },
    );
    write_unsigned(&mut message, 1);
    write_common(&mut message, id, name);
    write_unsigned(&mut message, 0);
    write_unsigned(&mut message, 0);
    write_message(output, &message);
}

#[cfg(test)]
#[path = "storage_cursor_tests.rs"]
mod tests;
