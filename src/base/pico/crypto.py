"""
AES-256-CBC encryption for WiFi credentials stored at rest.

The encryption key is derived from the device's unique MAC address using
SHA-256, so the provisioning.json file is useless without the physical device.

Note: This does not protect against a determined attacker with full physical
access to the device, as the same device can be used to derive the same key.
It protects against: filesystem image dumps, file exfiltration, casual reads.
"""

import ucryptolib
import uhashlib
import os
import ubinascii
import network

# CBC mode constant for ucryptolib.aes
_AES_CBC = 2
_BLOCK_SIZE = 16


def _get_key():
    """Derive AES-256 key from MAC address + fixed salt via SHA-256."""
    sta = network.WLAN(network.STA_IF)
    was_active = sta.active()
    if not was_active:
        sta.active(True)
    mac = sta.config('mac')
    if not was_active:
        sta.active(False)

    h = uhashlib.sha256(b'plasmadevice-v1-' + mac)
    return h.digest()  # 32 bytes -> AES-256


def encrypt_password(password):
    """
    Encrypt a password string with AES-256-CBC.
    Returns (iv_hex, ciphertext_hex) as plain strings.
    """
    key = _get_key()
    iv = os.urandom(_BLOCK_SIZE)

    # PKCS7 padding to block boundary
    pwd_bytes = password.encode('utf-8')
    pad_len = _BLOCK_SIZE - (len(pwd_bytes) % _BLOCK_SIZE)
    pwd_bytes += bytes([pad_len] * pad_len)

    cipher = ucryptolib.aes(key, _AES_CBC, iv)
    encrypted = cipher.encrypt(pwd_bytes)

    return (
        ubinascii.hexlify(iv).decode('ascii'),
        ubinascii.hexlify(encrypted).decode('ascii'),
    )


def decrypt_password(iv_hex, encrypted_hex):
    """
    Decrypt a password previously encrypted by encrypt_password.
    Returns the password as a plain string.
    """
    key = _get_key()
    iv = ubinascii.unhexlify(iv_hex)
    encrypted = ubinascii.unhexlify(encrypted_hex)

    cipher = ucryptolib.aes(key, _AES_CBC, iv)
    decrypted = cipher.decrypt(encrypted)

    # Remove PKCS7 padding
    pad_len = decrypted[-1]
    return decrypted[:-pad_len].decode('utf-8')
