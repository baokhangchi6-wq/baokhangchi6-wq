// Credits: estacc123-ux, yellowgreg

#pragma once
#include <cstdint>
#include <cstddef>
#define CommonHeader uint8_t tt; uint8_t memcat; uint8_t marked
// TValue (0x10 bytes)
// value(+0x00), extra/vec_z(+0x08), tt(+0x0C
// Type tags (Roblox-specific - includes LUA_TINTEGER)
#define LUA_TNIL            0
#define LUA_TBOOLEAN        1
#define LUA_TLIGHTUSERDATA  2
#define LUA_TNUMBER         3
#define LUA_TINTEGER        4   // Roblox addition
#define LUA_TVECTOR         5
#define LUA_TSTRING         6
#define LUA_TTABLE          7
#define LUA_TFUNCTION       8
#define LUA_TUSERDATA       9
#define LUA_TTHREAD         10  // 0x0A
#define LUA_TBUFFER         11  // 0x0B
#define LUA_TPROTO          12  // 0x0C (internal)
#define LUA_TUPVAL          13  // 0x0D (internal)

// Error codes
#define LUA_OK        0
#define LUA_YIELD     1
#define LUA_ERRRUN    2
#define LUA_ERRSYNTAX 3
#define LUA_ERRMEM    4
#define LUA_ERRERR    5
#define LUA_BREAK     6   // Luau-specific
// 0x7F = running sentinel (Roblox-specific, prevents self-resume)

// CallInfo flags
#define LUA_CALLINFO_RETURN (1 << 0)
#define LUA_CALLINFO_HANDLE (1 << 1)
#define LUA_CALLINFO_NATIVE (1 << 2)



#define VMValue_Raw(T) T
#define TSTRING_HASH_ENC     VMValue_Raw
#define LSTATE_STACKSIZE_ENC VMValue_Raw
#define CLOSURE_CONT_ENC     VMValue_Raw
#define CLOSURE_DEBUGNAME_ENC VMValue_Raw
#define PROTO_LINEINFO_ENC   VMValue_Raw
#define PROTO_SOURCE_ENC     VMValue_Raw
#define PROTO_ABSLINEINFO_ENC VMValue_Raw
#define PROTO_UPVALUES_ENC   VMValue_Raw
#define PROTO_LOCVARS_ENC    VMValue_Raw
#define PROTO_DEBUGNAME_ENC  VMValue_Raw
#define PROTO_DEBUGINSN_ENC  VMValue_Raw
#define PROTO_TYPEINFO_ENC   VMValue_Raw
#define PROTO_USERDATA_ENC   VMValue_Raw
#define TABLE_META_ENC       VMValue_Raw
#define UDATA_META_ENC       VMValue_Raw
#define GSTATE_TTNAME_ENC    VMValue_Raw
#define GSTATE_TMNAME_ENC    VMValue_Raw


// TString

struct TString {
    CommonHeader;               // 0x00-0x02 (tt=0x06)
    uint8_t padding;            // 0x03
    int16_t atom;               // 0x04 (fast-comparison ID; -1 = not assigned)
    int16_t atom2;              // 0x06 (Roblox second intern index; 0x8000 = not assigned)
    TString* next;              // 0x08 (next in hash chain)
    unsigned int hash;          // 0x10
    unsigned int len;           // 0x14
    char data[1];               // 0x18 (inline string bytes, null terminated)
};
// sizeof(TString) = 0x18 + len + 1

// CallInfo (0x28 bytes)
// Open-source order: base, func, top, savedpc, nresults, flags

struct CallInfo {
    StkId top;                          // 0x00 (frame ceiling)
    const Instruction* savedpc;         // 0x08
    StkId base;                         // 0x10 (first arg / frame base)
    StkId func;                         // 0x18 (ptr to function TValue on stack)
    int nresults;                       // 0x20 (expected return count; -1 = LUA_MULTRET)
    unsigned int flags;                 // 0x24 (LUA_CALLINFO_* bits)
};

// Closure
// Open-source order: CommonHeader, isC, nupvalues, stacksize, preload

struct Closure {
    CommonHeader;                       // 0x00-0x02 (tt=0x08)
    uint8_t stacksize;                  // 0x03 (max stack slots, cached from Proto)
    uint8_t nupvalues;                  // 0x04
    uint8_t isC;                        // 0x05 (0=Lua, nonzero=C)
    uint8_t preload;                    // 0x06
    uint8_t padding;                    // 0x07
    // 0x08: unknown (open-source has gclist here but Roblox moved it)
    uint8_t _pad08[8];                  // 0x08
    GCObject* gclist;                   // 0x10 (GC gray list chain)
    union {
        struct {                        // 0x18 - C closure
            lua_CFunction f;            // 0x18
            const char* debugname;      // 0x20
            lua_Continuation cont;      // 0x28
            TValue upvals[1];           // 0x30
        } c;
        struct {                        // 0x18 - Lua closure
            Proto* p;                   // 0x18
            TValue uprefs[1];           // 0x20
        } l;
    };
};

// Proto (0xB0 bytes)

struct Proto {
    CommonHeader;                       // 0x00-0x02 (tt=0x0C)
    uint8_t numparams;                  // 0x03 (or nups - order uncertain)
    uint8_t is_vararg;                  // 0x04
    uint8_t maxstacksize;               // 0x05
    uint8_t flags_byte1;                // 0x06
    uint8_t flags_byte2;                // 0x07
    void* gc_next;                      // 0x08 (GC linkage)
    GCObject* gclist;                   // 0x10
    void* interrupt_ctx;                // 0x18 (passed to g->cb.interrupt as 2nd arg)
    void* raw_bytecode_buf;             // 0x20 (compressed bytecode input)
    void* padding_28;                   // 0x28
    void* execdata;                     // 0x30 (native code data)
    TValue* k;                          // 0x38 (constants array)
    Instruction* code;                  // 0x40 (bytecode array, stored into ci->savedpc)
    void* padding_48;                   // 0x48
    uint8_t* lineinfo;                  // 0x50 (compressed lineinfo)
    TString* source;                    // 0x58 (source filename / chunkname)
    Proto** p;                          // 0x60 (nested proto array)
    int* abslineinfo;                   // 0x68 (baseline line info)
    TString** upvalues;                 // 0x70 (upvalue name array)
    struct LocVar* locvars;             // 0x78 (local variable array)
    TString* debugname;                 // 0x80
    uint32_t raw_buf_size;              // 0x88
    int32_t proto_index;                // 0x8C (index in parent's p[] array)
    int32_t sizelineinfo;               // 0x90
    uint8_t linegaplog2;                // 0x94
    uint8_t padding_95[3];              // 0x95-0x97
    uint32_t sizek;                     // 0x98
    uint32_t sizelocvars;               // 0x9C
    uint32_t sizep;                     // 0xA0
    uint32_t sizeupvalues;              // 0xA4
    uint32_t linedefined;               // 0xA8
    uint32_t sizecode;                  // 0xAC
};
// sizeof(Proto) = 0xB0

// LuaTable (0x30 bytes)

struct LuaNode {
    TValue val;                         // 0x00 (value TValue)
    // key: value(0x10), extra(0x18), packed tt+next(0x1C)
    union {
        struct {
            Value value;                // 0x10
            int extra;                  // 0x18
            unsigned tt : 4;            // 0x1C bits[3:0]
            int next : 28;              // 0x1C bits[31:4] (signed, in LuaNode units)
        };
    } key;
};
// sizeof(LuaNode) = 0x20

struct LuaTable {
    CommonHeader;                       // 0x00-0x02 (tt=0x07)
    uint8_t nodemask;                   // 0x03 ((1<<lsizenode)-1)
    uint8_t lsizenode;                  // 0x04
    uint8_t tmcache;                    // 0x05
    uint8_t readonly;                   // 0x06
    uint8_t safeenv;                    // 0x07
    int sizearray;                      // 0x08
    int lastfree;                       // 0x0C (integer index, not pointer)
    GCObject* gclist;                   // 0x10
    TValue* array;                      // 0x18
    LuaNode* node;                      // 0x20
    LuaTable* metatable;                // 0x28
};
// sizeof(LuaTable) = 0x30

// lua_Callbacks

struct lua_Callbacks {
    void* userdata;                                     // g+0x540  (cb+0x00)
    void (*interrupt)(lua_State* L, int gc);            // g+0x548  (cb+0x08)
    void (*panic)(lua_State* L, int errcode);           // g+0x550  (cb+0x10)
    void (*userthread)(lua_State* LP, lua_State* L);    // g+0x558  (cb+0x18)
    void (*unknown_hook)(void);                         // g+0x560  (cb+0x20)
    // remaining callback fields in g+0x568+ region
};

// global_State
// Combined allocation: lua_State at base+0x00, global_State at base+0x80
// sizeof(global_State) ≈ 0x4690 (total alloc = 0x4710, lua_State = 0x80)

struct global_State {
    size_t GCthreshold;                 // 0x00 (when totalbytes >= this, run GC step)
    size_t totalbytes;                  // 0x08 (0x4710 on init)

    lua_Alloc frealloc;                 // 0x10 (allocator function)
    void* ud;                           // 0x18 (allocator userdata)
    void* panic;                        // 0x20 (unprotected error handler; NULL on init)

    GCObject* gray;                     // 0x28 (active gray list head)
    void* unknown_30;                   // 0x30

    // String table(open-source: hash, nuse, size)
    uint32_t strt_size;                 // 0x38 (bucket count; 0x20 after luaS_resize)
    uint32_t strt_nuse;                 // 0x3C (interned string count)
    TString** strt_hash;                // 0x40 (bucket array)

    // GC parameters
    int32_t gcgoal;                     // 0x48 (200 on init; "gcpause" in Lua 5.1)
    int32_t gcstepmul;                  // 0x4C (200 on init)
    int32_t gcstepsize;                 // 0x50 (0x400 on init)

    uint8_t currentwhite;               // 0x54 (9 on init; "gccolor" in old docs)
    uint8_t gcstate;                    // 0x55 (GC phase; 2=sweep)

    // GC sentinel / traversal lists
    uint8_t gc_sentinel[16];            // 0x58 (dummy anchor object)
    GCObject* gc_list_1;                // 0x68 (init → &sentinel; likely grayagain)
    GCObject* gc_list_2;                // 0x70 (init → &sentinel; likely weak)

    // 0x78 through 0x30F: GC page lists, sweep state, etc.
    uint8_t gc_region[0x298];           // 0x78

    lua_State* mainthread;              // 0x310
    void* unknown_318;                  // 0x318

    // Metamethod name strings - 21 entries
    TString* tmname[21];                // 0x320
    // [0]="__index" [1]="__newindex" [2]="__mode" [3]="__namecall"
    // [4]="__call" [5]="__iter" [6]="__len" [7]="__eq"
    // [8]="__add" [9]="__sub" [10]="__mul" [11]="__div"
    // [12]="__idiv" [13]="__mod" [14]="__pow" [15]="__unm"
    // [16]="__lt" [17]="__le" [18]="__concat" [19]="__type"
    // [20]="__metatable"

    // Type name strings - 12 entries
    TString* ttname[12];                // 0x3C8
    // [0]="nil" [1]="boolean" [2]="userdata" [3]="number"
    // [4]="integer" [5]="vector" [6]="string" [7]="table"
    // [8]="function" [9]="userdata" [10]="thread" [11]="buffer"

    // Per-type metatables - 12 entries
    LuaTable* mt[12];                   // 0x428
    // indexed by LUA_T* type tag

    uint8_t unknown_488[16];            // 0x488

    // Registry (inline TValue)
    TValue registry;                    // 0x498
    // registry.value = Table* at +0x498
    // registry.tt = 7 (LUA_TTABLE) at +0x4A4

    uint32_t unknown_4a8;               // 0x4A8 (0x70000000 on init)
    uint8_t padding_4ac[12];            // 0x4AC

    // Roblox sandbox / callback hooks
    void* global_set_hook;              // 0x4B8

    uint8_t padding_4c0[0x28];          // 0x4C0

    void* useratom_cb;                  // 0x4E8 (atom assignment callback)
    void* native_executor;              // 0x4F0 (NCG function ptr; busy-waited by ncg_loop)

    uint8_t padding_4f8[0x48];          // 0x4F8

    // lua_Callbacks block
    void* cb_userdata;                  // 0x540
    void (*cb_interrupt)(lua_State* L, int gc); // 0x548
    void (*cb_panic)(lua_State* L, int errcode); // 0x550
    void (*cb_userthread)(lua_State* LP, lua_State* L); // 0x558
    void (*cb_unknown)(void);           // 0x560

    uint8_t extended_region[0x1058];    // 0x568 through ~0x15BF

    // Per-memory-category size tracking - 256 entries
    size_t memcatbytes[256];            // 0x15C0
    // memcatbytes[0] = 0x4710 on init (combined LG block)

    uint8_t roblox_data_1[0x1840];      // 0x1DC0
    // includes:
    // +0x3400 (from g): 0x800 bytes zeroed
    // +0x3C00 (from g): 0x400 bytes zeroed
    // +0x4000 (from g): 0x410 bytes zeroed (if FFlag set)
};

// lua_State (0x80 bytes)

struct lua_State {
    CommonHeader;                       // 0x00-0x02 (tt=0x0A = LUA_TTHREAD)
    uint8_t status;                     // 0x03
    // 0x00=OK, 0x01=LUA_YIELD, 0x06=LUA_BREAK, 0x7F=running

    uint8_t activememcat;               // 0x04
    bool isactive;                      // 0x05 (set true in resume_start)
    bool native_exec;                   // 0x06 (0x01=NCG path active; may be singlestep in open-source)
    uint8_t padding_07;                 // 0x07

    int stacksize;                      // 0x08
    int size_ci;                        // 0x0C (8 on init)

    uint8_t padding_10[8];              // 0x10

    global_State* global;               // 0x18 (= base + 0x80)
    StkId stack_last;                   // 0x20
    StkId top;                          // 0x28
    StkId stack;                        // 0x30 (allocation base)
    CallInfo* ci;                       // 0x38
    StkId base;                         // 0x40 (current frame base)

    uint8_t padding_48[0x10];           // 0x48

    CallInfo* end_ci;                   // 0x58
    CallInfo* base_ci;                  // 0x60

    unsigned short nCcalls;             // 0x68 (C call depth)
    unsigned short baseCcalls;          // 0x6A (yieldability tracking)

    uint8_t padding_6c[4];              // 0x6C

    LuaTable* gt;                       // 0x70 (_G global environment)
    UpVal* openupval;                   // 0x78 (linked list of open upvalues)
};
// sizeof(lua_State) = 0x80

// Static data addresses

// DAT_04ebdbc0 = nil TValue sentinel (luaH_getstr / luaT_gettmbyobj miss)
// DAT_04ebdcd0 = dummy LuaNode (Table->node for empty tables)
// DAT_04ebdd10 = opcode unshuffle table (256 bytes; file → runtime)
// DAT_04ebde10 = instruction size table (256 bytes; encoded)
// DAT_05daf138 = opcode dispatch table (256 * 8 bytes; runtime → handler)
// DAT_05dadd88 = lua_exception vtable
// DAT_05fabec8 = FFlag::LuauStacklessPcall (0x01 = enabled)
// DAT_05fabf10 = feature flag checked in lua_newstate
