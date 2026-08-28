package worldcommand

// LatestAcceptedReceipt returns the accepted receipt with the greatest
// canonical event sequence. Rejected receipts and receipts without an event do
// not contribute terminal outcome authority.
func (s *Store) LatestAcceptedReceipt() (Receipt, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var selected Receipt
	found := false
	var selectedSequence uint64
	for _, receipt := range s.receipts {
		if receipt.Disposition != DispositionAccepted || receipt.EventSequence == nil {
			continue
		}
		if !found || *receipt.EventSequence > selectedSequence {
			selected = cloneReceipt(receipt)
			selectedSequence = *receipt.EventSequence
			found = true
		}
	}
	return selected, found
}
