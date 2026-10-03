#![forbid(unsafe_code)]

mod storage_transfer;

fn main() {
    if let Err(reason) = storage_transfer::run(true) {
        eprintln!("trnm-storage-export: {reason}");
        std::process::exit(1);
    }
}
