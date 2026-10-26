#include "nostr.h"
#include <stdio.h>
#include "aes/aes.h"
#include "algo/base64.h"
#include "base64.h"
#include "cosmos/json_parser.h"
#include "fsm.h"
#include "gettext.h"
#include "layout2.h"
#include "messages.h"
#include "memzero.h"
#include "messages.pb.h"
#include "protect.h"
#include "ripemd160.h"
#include "rng.h"
#include "secp256k1.h"
#include "segwit_addr.h"
#include "sha3.h"
#include "stdint.h"
#include "transaction.h"
#include "util.h"
#include "zkp_bip340.h"

extern int ethereum_is_canonic(uint8_t v, uint8_t signature[64]);
extern int convert_bits(uint8_t *out, size_t *outlen, int outbits,
                        const uint8_t *in, size_t inlen, int inbits, int pad);

int nostr_get_pubkey(char *address, const uint8_t *public_key) {
  uint8_t data[65] = {0};
  size_t datalen = 0;

  // bech32_encode
  bech32_encoding enc = BECH32_ENCODING_BECH32;
  convert_bits(data, &datalen, 5, public_key, 32, 8, 1);

  return bech32_encode(address, "npub", data, datalen, enc);
}

bool nostr_sign_event(const NostrSignEvent *msg, const HDNode *node,
                      NostrSignedEvent *resp) {
  parsed_json_t json;
  parser_error_t err;
  uint16_t req_root_item_key_token_idx = 0;
  uint8_t unsignedEvent_bytes[2048] = {0};
  int unsignedEvent_index = 0;
  char sig[129] = {0};
  char id[65] = {0};
  char pk[65] = {0};
  char npub[92] = {0};
  int tlen;

  nostr_get_pubkey(npub, node->public_key + 1);
  if (!fsm_layoutSignMessage("Nostr", npub, msg->event.bytes,
                             msg->event.size)) {
    fsm_sendFailure(FailureType_Failure_ActionCancelled, NULL);
    layoutHome();
    return false;
  }

  data2hexaddr(node->public_key + 1, 32, pk);

  err = json_parse(&json, (const char *)msg->event.bytes, msg->event.size);
  if (err != parser_ok) {
    return err;
  }

  // [0, pubkey, j["created_at"], j["kind"], j["tags"], j["content"]]
  strcat((char *)unsignedEvent_bytes, "[0,");
  unsignedEvent_index += 3;

  // pubkey
  unsignedEvent_bytes[unsignedEvent_index++] = '"';
  data2hexaddr(node->public_key + 1, 32,
               (char *)(unsignedEvent_bytes + unsignedEvent_index));
  unsignedEvent_index += 64;
  unsignedEvent_bytes[unsignedEvent_index++] = '"';
  unsignedEvent_bytes[unsignedEvent_index++] = ',';

  // created_at
  err = object_get_value(&json, 0, "created_at", &req_root_item_key_token_idx);
  if (err != parser_ok) {
    fsm_sendFailure(FailureType_Failure_DataError, "created_at unexist");
    layoutHome();
    return false;
  }
  tlen = json.tokens[req_root_item_key_token_idx].end -
         json.tokens[req_root_item_key_token_idx].start;
  memcpy(unsignedEvent_bytes + unsignedEvent_index,
         msg->event.bytes + json.tokens[req_root_item_key_token_idx].start,
         tlen);
  unsignedEvent_index += tlen;
  unsignedEvent_bytes[unsignedEvent_index++] = ',';

  // kind
  err = object_get_value(&json, 0, "kind", &req_root_item_key_token_idx);
  if (err != parser_ok) {
    fsm_sendFailure(FailureType_Failure_DataError, "kind unexist");
    layoutHome();
    return false;
  }
  tlen = json.tokens[req_root_item_key_token_idx].end -
         json.tokens[req_root_item_key_token_idx].start;
  memcpy(unsignedEvent_bytes + unsignedEvent_index,
         msg->event.bytes + json.tokens[req_root_item_key_token_idx].start,
         tlen);
  unsignedEvent_index += tlen;
  unsignedEvent_bytes[unsignedEvent_index++] = ',';

  // tags
  err = object_get_value(&json, 0, "tags", &req_root_item_key_token_idx);
  if (err != parser_ok) {
    fsm_sendFailure(FailureType_Failure_DataError, "tags unexist");
    layoutHome();
    return false;
  }
  tlen = json.tokens[req_root_item_key_token_idx].end -
         json.tokens[req_root_item_key_token_idx].start;
  memcpy(unsignedEvent_bytes + unsignedEvent_index,
         msg->event.bytes + json.tokens[req_root_item_key_token_idx].start,
         tlen);
  unsignedEvent_index += tlen;
  unsignedEvent_bytes[unsignedEvent_index++] = ',';

  // content
  err = object_get_value(&json, 0, "content", &req_root_item_key_token_idx);
  if (err != parser_ok) {
    fsm_sendFailure(FailureType_Failure_DataError, "content unexist");
    layoutHome();
    return false;
  }
  unsignedEvent_bytes[unsignedEvent_index++] = '"';
  tlen = json.tokens[req_root_item_key_token_idx].end -
         json.tokens[req_root_item_key_token_idx].start;
  memcpy(unsignedEvent_bytes + unsignedEvent_index,
         msg->event.bytes + json.tokens[req_root_item_key_token_idx].start,
         tlen);
  unsignedEvent_index += tlen;
  unsignedEvent_bytes[unsignedEvent_index++] = '"';
  unsignedEvent_bytes[unsignedEvent_index++] = ']';

  uint8_t hash[32];
  uint8_t signature[64] = {0};
  SHA256_CTX ctx;
  sha256_Init(&ctx);
  sha256_Update(&ctx, unsignedEvent_bytes, unsignedEvent_index);
  sha256_Final(&ctx, hash);

  data2hexaddr(hash, 32, id);

  int ret = hdnode_bip340_sign_digest_internal(node, hash, signature);

  if (ret != 0) {
    fsm_sendFailure(FailureType_Failure_ProcessError, "Signing failed");
    return false;
  }
  data2hexaddr(signature, 64, sig);

  resp->event.size =
      snprintf((char *)resp->event.bytes, 512,
               "{\"pubkey\":\"%s\",\"id\":\"%s\",\"sig\":\"%s\"}", pk, id, sig);
  return true;
}

/* NIP-04 limits (DIY). The reply holds base64(ciphertext) + "?iv=" + 24
 * base64 characters of IV in at most 1562 characters, so the ciphertext can
 * be at most 1136 bytes and the plaintext at most 1135. The original code had
 * no limits: long messages, a long "iv" or a long peer key written by the
 * computer overflowed buffers, and bad padding made it write before the reply
 * buffer. */
#define NOSTR_IV_B64_LEN 24
#define NOSTR_MAX_CIPHERTEXT 1136
#define NOSTR_MAX_PLAINTEXT (NOSTR_MAX_CIPHERTEXT - 1)

/* hex2data() lets some non-hex characters through, so check first. */
static bool nostr_is_hex(const char *s, size_t n) {
  for (size_t i = 0; i < n; i++) {
    char c = s[i];
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
          (c >= 'A' && c <= 'F'))) {
      return false;
    }
  }
  return true;
}

/* Peer key: exactly 64 hex characters (x only); pk gets 0x02 || x. pk has
 * room for the NUL that hex2data always writes after the data. */
static bool nostr_parse_peer_pubkey(const char *hex, uint8_t pk[34]) {
  unsigned int len = 0;
  if (strnlen(hex, 65) != 64 || !nostr_is_hex(hex, 64)) return false;
  pk[0] = 0x02;
  return hex2data(hex, pk + 1, &len) == 0 && len == 32;
}

bool nostr_encrypt_message(NostrEncryptMessage *msg, const HDNode *node,
                           NostrEncryptedMessage *resp) {
  uint8_t shared_secret[65] = {0};
  uint8_t pk[34] = {0};
  char npub[92] = {0};

  size_t msg_len = strnlen(msg->msg, sizeof(msg->msg));
  if (!nostr_parse_peer_pubkey(msg->pubkey, pk)) {
    fsm_sendFailure(FailureType_Failure_DataError, "Invalid public key");
    layoutHome();
    return false;
  }
  if (msg_len > NOSTR_MAX_PLAINTEXT) {
    fsm_sendFailure(FailureType_Failure_DataError, "Message too long");
    layoutHome();
    return false;
  }

  if (msg->has_show_display && msg->show_display) {
    nostr_get_pubkey(npub, node->public_key + 1);
    if (!layoutNostrEncryptMessage("Nostr", true, npub,
                                   (const uint8_t *)msg->msg, msg_len, true)) {
      fsm_sendFailure(FailureType_Failure_ActionCancelled, NULL);
      layoutHome();
      return false;
    }
  }

  bool res = hdnode_bip340_get_shared_key_ln(node, pk, shared_secret) == 0;
  if (!res) {
    fsm_sendFailure(FailureType_Failure_ProcessError,
                    "Failed to derive shared secret.");
    return false;
  }

  uint8_t iv[16] = {0};
  char iv_b64[NOSTR_IV_B64_LEN + 1] = {0};

  random_buffer(iv, 16);
  base64_encode((char *)iv, 16, iv_b64, sizeof(iv_b64));

  /* PKCS#7 padding into a separate buffer. */
  uint8_t plain[NOSTR_MAX_CIPHERTEXT] = {0};
  size_t pad_len = 16 - (msg_len % 16);
  size_t ct_len = msg_len + pad_len;
  memcpy(plain, msg->msg, msg_len);
  memset(plain + msg_len, (int)pad_len, pad_len);

  aes_encrypt_ctx ctx = {0};
  uint8_t encrypted[NOSTR_MAX_CIPHERTEXT] = {0};
  bool ok = aes_encrypt_key256(shared_secret + 1, &ctx) == EXIT_SUCCESS &&
            aes_cbc_encrypt(plain, encrypted, (int)ct_len, iv, &ctx) ==
                EXIT_SUCCESS;
  memzero(plain, sizeof(plain));
  memzero(shared_secret, sizeof(shared_secret));
  memzero(&ctx, sizeof(ctx));
  if (!ok) {
    fsm_sendFailure(FailureType_Failure_DataError, "aes_cbc_encrypt failed");
    layoutHome();
    return false;
  }

  base64_encode((char *)encrypted, ct_len, (char *)resp->msg,
                sizeof(resp->msg));
  size_t out_len = strnlen(resp->msg, sizeof(resp->msg));
  if (out_len + 4 + NOSTR_IV_B64_LEN + 1 > sizeof(resp->msg)) {
    fsm_sendFailure(FailureType_Failure_DataError, "Message too long");
    layoutHome();
    return false;
  }
  memcpy(resp->msg + out_len, "?iv=", 4);
  memcpy(resp->msg + out_len + 4, iv_b64, NOSTR_IV_B64_LEN + 1);

  return true;
}

bool nostr_decrypt_message(NostrDecryptMessage *msg, const HDNode *node,
                           NostrDecryptedMessage *resp) {
  uint8_t shared_secret[65] = {0};
  uint8_t pk[34] = {0};
  char npub[92] = {0};
  uint8_t ct[sizeof(msg->msg)] = {0};
  uint8_t iv[18] = {0};

  if (!nostr_parse_peer_pubkey(msg->pubkey, pk)) {
    fsm_sendFailure(FailureType_Failure_DataError, "Invalid public key");
    layoutHome();
    return false;
  }

  if (msg->has_show_display && msg->show_display) {
    nostr_get_pubkey(npub, node->public_key + 1);
    if (!layoutNostrEncryptMessage(
            "Nostr", false, npub, (const uint8_t *)msg->msg,
            strnlen(msg->msg, sizeof(msg->msg)), true)) {
      fsm_sendFailure(FailureType_Failure_ActionCancelled, NULL);
      layoutHome();
      return false;
    }
  }

  char *iv_b64 = strstr(msg->msg, "?iv=");
  if (!iv_b64) {
    fsm_sendFailure(FailureType_Failure_DataError, "iv null");
    layoutHome();
    return false;
  }
  iv_b64[0] = '\0';
  iv_b64 += 4;

  /* The IV is 16 bytes: exactly 24 base64 characters. */
  char *end = NULL;
  if (strnlen(iv_b64, NOSTR_IV_B64_LEN + 1) == NOSTR_IV_B64_LEN) {
    end = b64tobin(iv, iv_b64);
  }
  if (!end || end - (char *)iv != 16) {
    fsm_sendFailure(FailureType_Failure_DataError, "iv error");
    layoutHome();
    return false;
  }

  /* Base64 decodes to at most 3/4 of its length, which fits in ct. */
  size_t ct_b64_len = strnlen(msg->msg, sizeof(msg->msg));
  end = NULL;
  if (ct_b64_len > 0 && ct_b64_len % 4 == 0 &&
      ct_b64_len / 4 * 3 <= sizeof(ct)) {
    end = b64tobin(ct, msg->msg);
  }
  size_t ct_len = end ? (size_t)(end - (char *)ct) : 0;
  if (ct_len == 0 || ct_len % 16 != 0 || ct_len >= sizeof(resp->msg)) {
    fsm_sendFailure(FailureType_Failure_DataError, "ct error");
    layoutHome();
    return false;
  }

  bool res = hdnode_bip340_get_shared_key_ln(node, pk, shared_secret) == 0;
  if (!res) {
    fsm_sendFailure(FailureType_Failure_ProcessError,
                    "Failed to derive shared secret.");
    return false;
  }

  aes_decrypt_ctx ctx = {0};
  bool ok = aes_decrypt_key256(shared_secret + 1, &ctx) == EXIT_SUCCESS &&
            aes_cbc_decrypt(ct, (unsigned char *)resp->msg, (int)ct_len, iv,
                            &ctx) == EXIT_SUCCESS;
  memzero(shared_secret, sizeof(shared_secret));
  memzero(&ctx, sizeof(ctx));
  if (!ok) {
    fsm_sendFailure(FailureType_Failure_DataError, "aes_cbc_decrypt failed");
    layoutHome();
    return false;
  }

  /* PKCS#7: 1..16 bytes, all equal to the count. */
  uint8_t pad_val = (uint8_t)resp->msg[ct_len - 1];
  bool pad_ok = pad_val >= 1 && pad_val <= 16 && pad_val <= ct_len;
  for (size_t i = 1; pad_ok && i <= pad_val; i++) {
    if ((uint8_t)resp->msg[ct_len - i] != pad_val) pad_ok = false;
  }
  if (!pad_ok) {
    memzero(resp->msg, sizeof(resp->msg));
    fsm_sendFailure(FailureType_Failure_DataError, "Invalid padding");
    layoutHome();
    return false;
  }
  resp->msg[ct_len - pad_val] = '\0';
  return true;
}

bool nostr_sign_schnorr(const NostrSignSchnorr *msg, const HDNode *node,
                        NostrSignedSchnorr *resp) {
  uint8_t hash[33] = {0};  // hex2data writes a NUL after the 32 bytes
  char npub[92] = {0};
  unsigned int len = 0;

  /* The hash must be exactly 32 bytes (64 hex characters). A longer string
   * used to be written past the end of the buffer. */
  if (strnlen(msg->hash, sizeof(msg->hash)) != 64 ||
      !nostr_is_hex(msg->hash, 64) || hex2data(msg->hash, hash, &len) != 0 ||
      len != 32) {
    fsm_sendFailure(FailureType_Failure_DataError, "Invalid hash");
    layoutHome();
    return false;
  }
  nostr_get_pubkey(npub, node->public_key + 1);

  if (!layoutSignSchnorrHash("Nostr", npub, msg->hash)) {
    fsm_sendFailure(FailureType_Failure_ActionCancelled, NULL);
    layoutHome();
    return false;
  }

  int ret =
      hdnode_bip340_sign_digest_internal(node, hash, resp->signature.bytes);

  if (ret != 0) {
    fsm_sendFailure(FailureType_Failure_ProcessError, "Signing failed");
    return false;
  }
  resp->signature.size = 64;

  return true;
}
