"""Restore the original header of an NCA that a cartridge-to-NSP conversion changed.

Format reference: https://switchbrew.org/wiki/NCA#Encryption
Converters change the distribution type from GameCard (1) to Download (0). This byte is in
header sector 1, so the NCA hash no longer matches its content ID. Restoring that byte must
reproduce the original hash exactly. Any other change still fails the content ID check.
"""

import ctypes

SECTOR_SIZE = 0x200
_ENCRYPT, _DECRYPT = 0, 1


def _load_crypto():
    """Return AES-128 ECB and CTR functions for the host system."""
    try:
        return _commoncrypto_backend()
    except OSError:
        return _openssl_backend()


def _commoncrypto_backend():
    lib = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
    lib.CCCrypt.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t,
                            ctypes.POINTER(ctypes.c_size_t)]
    lib.CCCryptorCreateWithMode.argtypes = [
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    lib.CCCryptorCreateWithMode.restype = ctypes.c_int32
    lib.CCCryptorUpdate.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p,
                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    lib.CCCryptorUpdate.restype = ctypes.c_int32
    lib.CCCryptorRelease.argtypes = [ctypes.c_void_p]

    def ecb(operation, key, data):
        output, moved = ctypes.create_string_buffer(len(data)), ctypes.c_size_t()
        # kCCAlgorithmAES = 0, kCCOptionECBMode = 2.
        if lib.CCCrypt(operation, 0, 2, key, len(key), None, data, len(data), output, len(data), ctypes.byref(moved)) \
                or moved.value != len(data):
            raise ValueError("AES operation failed.")
        return output.raw

    def ctr(key, iv, data):
        reference = ctypes.c_void_p()
        # kCCDecrypt = 1, kCCModeCTR = 4, kCCAlgorithmAES = 0, ccNoPadding = 0, kCCModeOptionCTR_BE = 2.
        if lib.CCCryptorCreateWithMode(1, 4, 0, 0, iv, key, len(key), None, 0, 0, 2, ctypes.byref(reference)):
            raise ValueError("AES operation failed.")
        try:
            output, moved = ctypes.create_string_buffer(len(data)), ctypes.c_size_t()
            if lib.CCCryptorUpdate(reference, data, len(data), output, len(data), ctypes.byref(moved)) \
                    or moved.value != len(data):
                raise ValueError("AES operation failed.")
            return output.raw
        finally:
            lib.CCCryptorRelease(reference)

    return ecb, ctr


def _openssl_backend():
    lib = None
    for name in ("libcrypto.so.3", "libcrypto.so"):
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    if lib is None:
        raise OSError("No AES backend: CommonCrypto or OpenSSL is required.")
    lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
    lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
    lib.EVP_aes_128_ecb.restype = ctypes.c_void_p
    lib.EVP_aes_128_ctr.restype = ctypes.c_void_p
    for name in ("EVP_EncryptInit_ex", "EVP_DecryptInit_ex"):
        getattr(lib, name).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_char_p, ctypes.c_char_p]
    lib.EVP_CIPHER_CTX_set_padding.argtypes = [ctypes.c_void_p, ctypes.c_int]
    for name in ("EVP_EncryptUpdate", "EVP_DecryptUpdate"):
        getattr(lib, name).argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
                                       ctypes.c_char_p, ctypes.c_int]
    for name in ("EVP_EncryptFinal_ex", "EVP_DecryptFinal_ex"):
        getattr(lib, name).argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
    init = {0: lib.EVP_EncryptInit_ex, 1: lib.EVP_DecryptInit_ex}
    update = {0: lib.EVP_EncryptUpdate, 1: lib.EVP_DecryptUpdate}

    def ecb(operation, key, data):
        context = lib.EVP_CIPHER_CTX_new()
        if not context:
            raise ValueError("AES operation failed.")
        try:
            if init[operation](context, lib.EVP_aes_128_ecb(), None, key, None) != 1 \
                    or lib.EVP_CIPHER_CTX_set_padding(context, 0) != 1:
                raise ValueError("AES operation failed.")
            output = ctypes.create_string_buffer(len(data) + 15)
            written = ctypes.c_int()
            # XTS calls this with whole blocks, so one update moves all bytes.
            if update[operation](context, output, ctypes.byref(written), data, len(data)) != 1 \
                    or written.value != len(data):
                raise ValueError("AES operation failed.")
            return output.raw[:len(data)]
        finally:
            lib.EVP_CIPHER_CTX_free(context)

    def ctr(key, iv, data):
        context = lib.EVP_CIPHER_CTX_new()
        if not context:
            raise ValueError("AES operation failed.")
        try:
            # OpenSSL CTR increments the whole block as a big-endian integer, as Nintendo does.
            if lib.EVP_DecryptInit_ex(context, lib.EVP_aes_128_ctr(), None, key, iv) != 1:
                raise ValueError("AES operation failed.")
            output = ctypes.create_string_buffer(len(data) + 15)
            written = ctypes.c_int()
            if lib.EVP_DecryptUpdate(context, output, ctypes.byref(written), data, len(data)) != 1 \
                    or written.value != len(data):
                raise ValueError("AES operation failed.")
            return output.raw[:len(data)]
        finally:
            lib.EVP_CIPHER_CTX_free(context)

    return ecb, ctr


_aes_ecb, _aes_ctr = _load_crypto()


def xts_aes128(operation, key, data, tweak_value):
    """AES-128-XTS. Nintendo encodes the sector number as a big-endian tweak."""
    if len(key) != 32 or len(data) % 16:
        raise ValueError("XTS needs a 32-byte key and whole blocks.")
    tweak = bytearray(_aes_ecb(_ENCRYPT, key[16:], tweak_value.to_bytes(16, "big")))
    output = bytearray()
    for offset in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[offset:offset + 16], tweak))
        output += bytes(a ^ b for a, b in zip(_aes_ecb(operation, key[:16], block), tweak))
        carry = tweak[15] >> 7
        for index in range(15, 0, -1):
            tweak[index] = ((tweak[index] << 1) | (tweak[index - 1] >> 7)) & 0xFF
        tweak[0] = ((tweak[0] << 1) & 0xFF) ^ (0x87 if carry else 0)
    return bytes(output)


def gamecard_header(header, header_key):
    """Return the header with the GameCard distribution type, or None if it is not a converted NCA3 header."""
    if len(header) < 2 * SECTOR_SIZE:
        return None
    plain = bytearray(xts_aes128(_DECRYPT, header_key, header[SECTOR_SIZE:2 * SECTOR_SIZE], 1))
    if plain[:4] != b"NCA3" or plain[4] != 0:
        return None
    plain[4] = 1
    return header[:SECTOR_SIZE] + xts_aes128(_ENCRYPT, header_key, bytes(plain), 1) + header[2 * SECTOR_SIZE:]
