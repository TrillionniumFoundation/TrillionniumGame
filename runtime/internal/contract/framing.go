package contract

import (
	"encoding/binary"
	"errors"
	"fmt"
	"math"
)

// frame is a small, language-neutral canonical binary encoder. Every variable
// field is length-prefixed using an unsigned big-endian 32-bit integer. It
// carries errors instead of panicking so every public contract operation fails
// closed even when handed an adversarially large byte slice.
type frame struct {
	data []byte
	err  error
}

func newFrame(domain string) frame {
	return frame{data: append(append([]byte(nil), domain...), 0)}
}

func (f frame) string(value string) frame {
	return f.bytes([]byte(value))
}

func (f frame) bytes(value []byte) frame {
	if f.err != nil {
		return f
	}
	if uint64(len(value)) > uint64(math.MaxUint32) {
		f.err = errors.New("canonical field exceeds uint32 length")
		return f
	}
	var size [4]byte
	binary.BigEndian.PutUint32(size[:], uint32(len(value)))
	f.data = append(f.data, size[:]...)
	f.data = append(f.data, value...)
	return f
}

func (f frame) u32(value uint32) frame {
	if f.err != nil {
		return f
	}
	var raw [4]byte
	binary.BigEndian.PutUint32(raw[:], value)
	f.data = append(f.data, raw[:]...)
	return f
}

func (f frame) u64(value uint64) frame {
	if f.err != nil {
		return f
	}
	var raw [8]byte
	binary.BigEndian.PutUint64(raw[:], value)
	f.data = append(f.data, raw[:]...)
	return f
}

func (f frame) i64(value int64) frame {
	return f.u64(uint64(value))
}

func (f frame) digest(value Digest) (frame, error) {
	if f.err != nil {
		return f, f.err
	}
	raw, err := value.Bytes()
	if err != nil {
		return frame{}, fmt.Errorf("invalid digest: %w", err)
	}
	f.data = append(f.data, raw[:]...)
	return f, nil
}

func (f frame) result() ([]byte, error) {
	if f.err != nil {
		return nil, f.err
	}
	return f.data, nil
}
