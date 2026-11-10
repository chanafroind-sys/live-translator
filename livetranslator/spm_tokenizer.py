"""Read a SentencePiece .model file without the `sentencepiece` package.

Windows Smart App Control blocks the unsigned `sentencepiece` DLL on this
machine, so we parse the model's protobuf ourselves and rebuild it as a
Hugging Face `tokenizers` tokenizer (which Windows allows to load).
"""
from __future__ import annotations

import struct

from tokenizers import Tokenizer, decoders, models, normalizers, pre_tokenizers


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, wire_type, value) for one protobuf message."""
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _read_varint(buf, i)
        elif wt == 1:
            val, i = buf[i:i + 8], i + 8
        elif wt == 2:
            n, i = _read_varint(buf, i)
            val, i = buf[i:i + n], i + n
        elif wt == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        yield num, wt, val


def load_spm(path: str) -> tuple[Tokenizer, str]:
    """Return (tokenizer, model_type) built from a SentencePiece model file."""
    with open(path, "rb") as f:
        data = f.read()

    pieces: list[tuple[str, float, int]] = []
    model_type = 1  # 1 = unigram, 2 = BPE
    for num, _, val in _fields(data):
        if num == 1:  # SentencePiece
            piece, score, ptype = "", 0.0, 1
            for n2, _, v2 in _fields(val):
                if n2 == 1:
                    piece = v2.decode("utf-8")
                elif n2 == 2:
                    score = struct.unpack("<f", v2)[0]
                elif n2 == 3:
                    ptype = v2
            pieces.append((piece, score, ptype))
        elif num == 2:  # TrainerSpec
            for n2, _, v2 in _fields(val):
                if n2 == 3:
                    model_type = v2

    unk_id = next((i for i, p in enumerate(pieces) if p[2] == 2), 0)
    if model_type == 2:
        vocab = {p: i for i, (p, _, _) in enumerate(pieces)}
        # SentencePiece BPE merges are implied by piece scores; derive them.
        merges = []
        for piece, _, _ in sorted(pieces, key=lambda x: -x[1]):
            for k in range(1, len(piece)):
                a, b = piece[:k], piece[k:]
                if a in vocab and b in vocab:
                    merges.append((a, b))
                    break
        model = models.BPE(vocab, merges, unk_token=pieces[unk_id][0], fuse_unk=True)
        kind = "bpe"
    else:
        model = models.Unigram([(p, s) for p, s, _ in pieces], unk_id=unk_id, byte_fallback=False)
        kind = "unigram"

    tok = Tokenizer(model)
    tok.normalizer = normalizers.NFKC()
    tok.pre_tokenizer = pre_tokenizers.Metaspace(replacement="▁", prepend_scheme="always")
    tok.decoder = decoders.Metaspace(replacement="▁", prepend_scheme="always")
    return tok, kind
