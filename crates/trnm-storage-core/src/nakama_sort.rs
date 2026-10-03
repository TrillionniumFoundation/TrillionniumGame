// Copyright 2009 The Go Authors. All rights reserved.
// Copyright 2022 The Go Authors. All rights reserved.
// Modified: bounded Rust translation of Go 1.26.5 sort.Sort's ordinal permutation.
// Original source: golang/go c19862e5f8415b4f24b189d065ed739517c548ba,
// src/sort/sort.go and src/sort/zsortinterface.go.
// BSD license: third_party/go-sort/LICENSE; source identities are registered in
// contracts/storage/nakama-sort-source-lock-v1.json.
// Preserve comparison, swap and branch order; equal keys have no ordinal tie.
// This helper only covers at most 100 canonical-owner batch occurrences.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum SortBoundError {
    TooManyOperations,
}

pub(crate) fn go1265_sort_ordinals<F>(data: &mut [usize], less: F) -> Result<(), SortBoundError>
where
    F: FnMut(usize, usize) -> bool,
{
    if data.len() > 100 {
        return Err(SortBoundError::TooManyOperations);
    }
    let n = data.len();
    if n > 1 {
        let limit = usize::BITS - n.leading_zeros();
        Sorter { data, less }.pdqsort(0, n as isize, limit as isize);
    }
    Ok(())
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum Hint {
    Unknown,
    Increasing,
    Decreasing,
}

struct Sorter<'a, F> {
    data: &'a mut [usize],
    less: F,
}

impl<F: FnMut(usize, usize) -> bool> Sorter<'_, F> {
    fn less(&mut self, a: isize, b: isize) -> bool {
        (self.less)(self.data[a as usize], self.data[b as usize])
    }
    fn swap(&mut self, a: isize, b: isize) {
        self.data.swap(a as usize, b as usize);
    }
    fn insertion(&mut self, a: isize, b: isize) {
        for i in a + 1..b {
            let mut j = i;
            while j > a && self.less(j, j - 1) {
                self.swap(j, j - 1);
                j -= 1;
            }
        }
    }
    fn sift_down(&mut self, lo: isize, hi: isize, first: isize) {
        let mut root = lo;
        loop {
            let mut child = 2 * root + 1;
            if child >= hi {
                break;
            }
            if child + 1 < hi && self.less(first + child, first + child + 1) {
                child += 1;
            }
            if !self.less(first + root, first + child) {
                return;
            }
            self.swap(first + root, first + child);
            root = child;
        }
    }
    fn heap(&mut self, a: isize, b: isize) {
        let hi = b - a;
        let mut i = (hi - 1) / 2;
        while i >= 0 {
            self.sift_down(i, hi, a);
            i -= 1;
        }
        let mut i = hi - 1;
        while i >= 0 {
            self.swap(a, a + i);
            self.sift_down(0, i, a);
            i -= 1;
        }
    }
    fn pdqsort(&mut self, mut a: isize, mut b: isize, mut limit: isize) {
        let mut was_balanced = true;
        let mut was_partitioned = true;
        loop {
            let length = b - a;
            if length <= 12 {
                self.insertion(a, b);
                return;
            }
            if limit == 0 {
                self.heap(a, b);
                return;
            }
            if !was_balanced {
                self.break_patterns(a, b);
                limit -= 1;
            }
            let (mut pivot, mut hint) = self.choose_pivot(a, b);
            if hint == Hint::Decreasing {
                self.reverse(a, b);
                pivot = (b - 1) - (pivot - a);
                hint = Hint::Increasing;
            }
            if was_balanced
                && was_partitioned
                && hint == Hint::Increasing
                && self.partial_insertion(a, b)
            {
                return;
            }
            if a > 0 && !self.less(a - 1, pivot) {
                a = self.partition_equal(a, b, pivot);
                continue;
            }
            let (mid, partitioned) = self.partition(a, b, pivot);
            was_partitioned = partitioned;
            let left_len = mid - a;
            let right_len = b - mid;
            let threshold = length / 8;
            if left_len < right_len {
                was_balanced = left_len >= threshold;
                self.pdqsort(a, mid, limit);
                a = mid + 1;
            } else {
                was_balanced = right_len >= threshold;
                self.pdqsort(mid + 1, b, limit);
                b = mid;
            }
        }
    }
    fn partition(&mut self, a: isize, b: isize, pivot: isize) -> (isize, bool) {
        self.swap(a, pivot);
        let (mut i, mut j) = (a + 1, b - 1);
        while i <= j && self.less(i, a) {
            i += 1;
        }
        while i <= j && !self.less(j, a) {
            j -= 1;
        }
        if i > j {
            self.swap(j, a);
            return (j, true);
        }
        self.swap(i, j);
        i += 1;
        j -= 1;
        loop {
            while i <= j && self.less(i, a) {
                i += 1;
            }
            while i <= j && !self.less(j, a) {
                j -= 1;
            }
            if i > j {
                break;
            }
            self.swap(i, j);
            i += 1;
            j -= 1;
        }
        self.swap(j, a);
        (j, false)
    }
    fn partition_equal(&mut self, a: isize, b: isize, pivot: isize) -> isize {
        self.swap(a, pivot);
        let (mut i, mut j) = (a + 1, b - 1);
        loop {
            while i <= j && !self.less(a, i) {
                i += 1;
            }
            while i <= j && self.less(a, j) {
                j -= 1;
            }
            if i > j {
                break;
            }
            self.swap(i, j);
            i += 1;
            j -= 1;
        }
        i
    }
    fn partial_insertion(&mut self, a: isize, b: isize) -> bool {
        let mut i = a + 1;
        for _ in 0..5 {
            while i < b && !self.less(i, i - 1) {
                i += 1;
            }
            if i == b {
                return true;
            }
            if b - a < 50 {
                return false;
            }
            self.swap(i, i - 1);
            if i - a >= 2 {
                let mut j = i - 1;
                // Go uses j >= 1 here, intentionally not j >= a + 1.
                while j >= 1 {
                    if !self.less(j, j - 1) {
                        break;
                    }
                    self.swap(j, j - 1);
                    j -= 1;
                }
            }
            if b - i >= 2 {
                let mut j = i + 1;
                while j < b {
                    if !self.less(j, j - 1) {
                        break;
                    }
                    self.swap(j, j - 1);
                    j += 1;
                }
            }
        }
        false
    }
    fn break_patterns(&mut self, a: isize, b: isize) {
        let length = b - a;
        if length >= 8 {
            let mut random = length as u64;
            let length_u = length as usize;
            let modulus = 1_usize << (usize::BITS - length_u.leading_zeros());
            for idx in a + length / 4 * 2 - 1..=a + length / 4 * 2 + 1 {
                random ^= random << 13;
                random ^= random >> 7;
                random ^= random << 17;
                let mut other = (random as usize & (modulus - 1)) as isize;
                if other >= length {
                    other -= length;
                }
                self.swap(idx, a + other);
            }
        }
    }
    fn choose_pivot(&mut self, a: isize, b: isize) -> (isize, Hint) {
        let length = b - a;
        let mut swaps = 0;
        let (mut i, mut j, mut k) = (a + length / 4, a + length / 4 * 2, a + length / 4 * 3);
        if length >= 8 {
            if length >= 50 {
                i = self.median(i - 1, i, i + 1, &mut swaps);
                j = self.median(j - 1, j, j + 1, &mut swaps);
                k = self.median(k - 1, k, k + 1, &mut swaps);
            }
            j = self.median(i, j, k, &mut swaps);
        }
        (
            j,
            match swaps {
                0 => Hint::Increasing,
                12 => Hint::Decreasing,
                _ => Hint::Unknown,
            },
        )
    }
    fn order2(&mut self, a: isize, b: isize, swaps: &mut usize) -> (isize, isize) {
        if self.less(b, a) {
            *swaps += 1;
            (b, a)
        } else {
            (a, b)
        }
    }
    fn median(&mut self, a: isize, b: isize, c: isize, swaps: &mut usize) -> isize {
        let (a, b) = self.order2(a, b, swaps);
        let (b, c) = self.order2(b, c, swaps);
        let (_, b) = self.order2(a, b, swaps);
        let _ = c;
        b
    }
    fn reverse(&mut self, a: isize, b: isize) {
        let (mut i, mut j) = (a, b - 1);
        while i < j {
            self.swap(i, j);
            i += 1;
            j -= 1;
        }
    }
}

#[cfg(test)]
mod draft_tests {
    use super::*;

    fn plan(keys: &[&str]) -> Vec<usize> {
        let mut order: Vec<_> = (0..keys.len()).collect();
        go1265_sort_ordinals(&mut order, |a, b| keys[a].as_bytes() < keys[b].as_bytes()).unwrap();
        order
    }

    #[test]
    fn source_predicted_thirteen_operations_change_last_duplicate() {
        // SOURCE-MODEL golden, not independently executed Go1.26.5 result.
        assert_eq!(
            plan(&["b", "b", "a", "a", "a", "a", "a", "a", "a", "a", "a", "a", "a"]),
            vec![6, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 1, 0]
        );
    }

    #[test]
    fn insertion_boundary_keeps_occurrences_and_original_ordinals() {
        assert_eq!(plan(&["b", "a", "a"]), vec![1, 2, 0]);
        assert_eq!(plan(&["a"; 100]), (0..100).collect::<Vec<_>>());
    }

    #[test]
    fn oversized_input_rejects_before_comparison_or_permutation() {
        let mut order: Vec<_> = (0..101).collect();
        let before = order.clone();
        assert_eq!(
            go1265_sort_ordinals(&mut order, |_, _| panic!("comparison must not run")),
            Err(SortBoundError::TooManyOperations)
        );
        assert_eq!(order, before);
    }

    #[test]
    fn binary_thirteen_permutations_preserve_occurrences_and_key_order() {
        for mask in 0_u16..8192 {
            let keys: Vec<_> = (0..13)
                .map(|i| if mask & (1 << i) == 0 { "a" } else { "b" })
                .collect();
            let order = plan(&keys);
            let mut membership = order.clone();
            membership.sort_unstable();
            assert_eq!(membership, (0..13).collect::<Vec<_>>());
            assert!(order.windows(2).all(|w| keys[w[0]] <= keys[w[1]]));
        }
    }
}
