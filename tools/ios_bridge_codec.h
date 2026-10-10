/* Native iOS zlib. Output limit also bounds decompression of untrusted URL data. */
extern void* malloc(size_t);
extern void free(void*);
extern unsigned long compressBound(unsigned long);
extern int compress2(unsigned char*, unsigned long*, const unsigned char*, unsigned long, int);
extern int uncompress(unsigned char*, unsigned long*, const unsigned char*, unsigned long);
extern unsigned char* CC_SHA256(const void*, unsigned int, unsigned char*);

static bool ms_bridge_hash_matches(id payload, const char* expected) {
    if (!expected || strlen(expected) != 64) return false;
    id error = NULL;
    // Sorted keys + unescaped slashes match the bridge's canonical top-level JSON.
    id data = ((id (*)(id, SEL, id, unsigned long, id*))objc_msgSend)(objc_getClass("NSJSONSerialization"),
        sel_registerName("dataWithJSONObject:options:error:"), payload, 10, &error);
    if (!data || error) return false;
    unsigned long length = ((unsigned long (*)(id, SEL))objc_msgSend)(data, sel_registerName("length"));
    if (!length || length > 524288) return false;
    const void* bytes = ((const void* (*)(id, SEL))objc_msgSend)(data, sel_registerName("bytes"));
    unsigned char digest[32];
    if (!CC_SHA256(bytes, (unsigned int)length, digest)) return false;
    char hex[65];
    for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    return strcmp(hex, expected) == 0;
}

static id ms_bridge_codec(id data, bool encode) {
    if (!data) return NULL;
    unsigned long length = ((unsigned long (*)(id, SEL))objc_msgSend)(data, sel_registerName("length"));
    if (!length || length > 524288) return NULL;
    const unsigned char* bytes = ((const unsigned char* (*)(id, SEL))objc_msgSend)(data, sel_registerName("bytes"));
    unsigned long capacity = encode ? compressBound(length) : 524288;
    unsigned char* buffer = malloc(capacity);
    if (!buffer) return NULL;
    int status = encode ? compress2(buffer, &capacity, bytes, length, 6)
                        : uncompress(buffer, &capacity, bytes, length);
    id result = status == 0 ? ((id (*)(id, SEL, const void*, unsigned long))objc_msgSend)(
        objc_getClass("NSData"), sel_registerName("dataWithBytes:length:"), buffer, capacity) : NULL;
    free(buffer);
    return result;
}
