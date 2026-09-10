    #[test]
    fn batch_validation_rejects_empty_duplicate_and_excess() {
        assert_eq!(
            validate_batch(&[]).unwrap_err().reason(),
            "invalid_storage_batch_size"
        );
        let duplicate = vec![write(1), write(1)];
        assert_eq!(
            validate_batch(&duplicate).unwrap_err().reason(),
            "duplicate_storage_key_in_batch"
        );
        let excess = (0..=MAX_BATCH_OPERATIONS)
            .map(|index| write(u8::try_from(index + 1).unwrap()))
            .collect::<Vec<_>>();
        assert_eq!(
            validate_batch(&excess).unwrap_err().reason(),
            "invalid_storage_batch_size"
        );
    }
