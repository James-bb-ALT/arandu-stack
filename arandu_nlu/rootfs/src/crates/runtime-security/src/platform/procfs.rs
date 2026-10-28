pub(super) fn smaps_reports_locked(smaps: &str, address: usize, length: usize) -> bool {
    if length == 0 {
        return false;
    }
    let Some(last_byte) = address.checked_add(length.saturating_sub(1)) else {
        return false;
    };
    let mut selected = false;
    let mut locked_kib = None;
    let mut locked_flag = false;

    for line in smaps.lines() {
        if let Some((start, end)) = parse_mapping_range(line) {
            if selected {
                return mapping_is_locked(locked_kib, locked_flag, length);
            }
            selected = start <= address && last_byte < end;
            locked_kib = None;
            locked_flag = false;
        } else if selected {
            if let Some(value) = line.strip_prefix("Locked:") {
                locked_kib = parse_kib(value);
            } else if let Some(value) = line.strip_prefix("VmFlags:") {
                locked_flag = value.split_ascii_whitespace().any(|flag| flag == "lo");
            }
        }
    }

    selected && mapping_is_locked(locked_kib, locked_flag, length)
}

fn parse_mapping_range(line: &str) -> Option<(usize, usize)> {
    let range = line.split_ascii_whitespace().next()?;
    let (start, end) = range.split_once('-')?;
    Some((
        usize::from_str_radix(start, 16).ok()?,
        usize::from_str_radix(end, 16).ok()?,
    ))
}

fn parse_kib(value: &str) -> Option<u64> {
    let mut fields = value.split_ascii_whitespace();
    let amount = fields.next()?.parse::<u64>().ok()?;
    (fields.next() == Some("kB") && fields.next().is_none()).then_some(amount)
}

fn mapping_is_locked(locked_kib: Option<u64>, locked_flag: bool, length: usize) -> bool {
    let Some(bytes) = locked_kib.and_then(|value| value.checked_mul(1024)) else {
        return false;
    };
    locked_flag && bytes >= u64::try_from(length).unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE_TECNICA_SMAPS: &str = "\
1000-3000 rw-p 00000000 00:00 0 /FIXTURE_TECNICA
Size:                  8 kB
Locked:                8 kB
VmFlags: rd wr mr mw me lo ac sd
";

    #[test]
    fn smaps_parser_requires_range_count_and_lock_flag() {
        let no_flag = FIXTURE_TECNICA_SMAPS.replace(" lo", "");
        let too_small = FIXTURE_TECNICA_SMAPS
            .replace("Locked:                8 kB", "Locked:                1 kB");

        assert!(smaps_reports_locked(FIXTURE_TECNICA_SMAPS, 0x1000, 4096));
        assert!(!smaps_reports_locked(&no_flag, 0x1000, 4096));
        assert!(!smaps_reports_locked(&too_small, 0x1000, 4096));
        assert!(!smaps_reports_locked(FIXTURE_TECNICA_SMAPS, 0x4000, 4096));
        assert!(!smaps_reports_locked(FIXTURE_TECNICA_SMAPS, 0x1000, 0));
    }
}
