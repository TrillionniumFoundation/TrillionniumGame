#![forbid(unsafe_code)]

mod storage_transfer;

fn main() {
    if let Err(reason) = storage_transfer::run(false) {
        eprintln!("trnm-storage-import: {reason}");
        std::process::exit(1);
    }
}
