#pragma once

#include <cstdint>

// Roblox iOS (ARM64) - Luau VM offsets
// Version: 2.720.1164

// Confidence levels:
//   HIGH = verified directly 
//   MED  = inferred from context, consistent with Luau source patterns
//   LOW  = field confirmed to exist, meaning is a best guess

namespace offsets
{
    // Error reporting
    // All the "attempt to ..." functions. They never return, so don't expect them to.

    constexpr std::uintptr_t luaG_runerror        = 0x03f660f0; // [HIGH] vsnprintf + luaD_throw(ERRRUN); never returns
    constexpr std::uintptr_t luaG_typeerror       = 0x03f660c0; // [HIGH] "attempt to %s a %s value"; calls luaO_tostring first
    constexpr std::uintptr_t luaG_concaterror     = 0x03f66184; // [HIGH] "attempt to concatenate %s with %s"
    constexpr std::uintptr_t luaG_aritherror      = 0x03f661c8; // [HIGH] "attempt to perform arithmetic on a %s value"
    constexpr std::uintptr_t luaG_indexerror      = 0x03f662b4; // [HIGH] "attempt to index %s with %s"
    constexpr std::uintptr_t luaG_ordererror      = 0x03f66240; // [HIGH] "attempt to compare %s %s %s"
    constexpr std::uintptr_t luaG_toobig          = 0x03f6d648; // [HIGH]  string > 0x40000000 bytes; never returns


    // Execution and call stack
    // ci->func lives at +0x18,
    // not +0x00. Every public reference that says otherwise is wrong.

    constexpr std::uintptr_t luaD_precall         = 0x03f7e9f8; // [HIGH] (L, StkId func, int nresults); sets up a new CallInfo frame
    constexpr std::uintptr_t luau_poscall         = 0x03f7ebcc; // [HIGH] (L, StkId firstResult); tears down frame, copies results back (was luaD_poscall)
    constexpr std::uintptr_t tryfuncTM            = 0x03f81e98; // [HIGH] __call metamethod handler inside luaD_precall; throws type error if __call is absent
    constexpr std::uintptr_t luaD_throw           = 0x03f66e08; // [HIGH] C++ __cxa_throw wrapper, NOT longjmp; sizeof(lua_exception) = 0x18
    constexpr std::uintptr_t luaD_reallocstack    = 0x03f66e4c; // [HIGH] grows the value stack allocation (takes 3 arguments: L, newsize, fornewci)
    constexpr std::uintptr_t luaD_growCI          = 0x03f670a8; // [HIGH] grows CallInfo array when L->ci >= L->end_ci
    constexpr std::uintptr_t luaD_reallocCI       = 0x03f67010; // [HIGH] reallocs CallInfo array to a new size
    constexpr std::uintptr_t luaD_seterrorobj     = 0x03f67454; // [HIGH] sets the error TValue on the stack from an errcode
    constexpr std::uintptr_t luaD_rawrunprotected = 0x03f66d48; // [HIGH] (L, fn, ud) -> 0; sets up C++ try‑catch
    constexpr std::uintptr_t luaD_initstack       = 0x03f6f478; // [HIGH] (L, mainthread); allocates CI array and value stack
    constexpr std::uintptr_t resume_continue      = 0x03f67d40; // [HIGH] post-resume driver; runs Lua frames and C continuations (was "resume_execute_loop")


    // VM interpreter
    // The real interpreter is luaV_execute. The dispatch function just checks
    // L+0x06 and routes to native or bytecode. Ghidra can't recover the jump
    // table so don't bother trying.

    constexpr std::uintptr_t luaV_execute_dispatch = 0x03f7747c; // [HIGH] trampoline; reads L+0x06 native_exec byte; routes to NCG or bytecode
    constexpr std::uintptr_t luaV_ncg_loop         = 0x03f77490; // [HIGH] NCG native code execution loop (was luaV_execute_native)
    constexpr std::uintptr_t luaV_ncg_call         = 0x03f772e0; // [HIGH] NCG call trampoline
    constexpr std::uintptr_t luaV_execute          = 0x03f7b00c; // [HIGH] real bytecode interpreter; opcode jump table at opcode_dispatch_table
    constexpr std::uintptr_t opcode_CALL           = 0x03f7e9f4; // [HIGH] CALL handler (falls through to luaD_precall)


    // Thread and coroutine management

    constexpr std::uintptr_t lua_resume           = 0x03f674dc; // [HIGH] public API - calls resume_start → rawrunprotected → resume_finish
    constexpr std::uintptr_t resume_start         = 0x03f67530; // [HIGH] static helper; checks coroutine state, copies nCcalls, sets isactive
    constexpr std::uintptr_t resume               = 0x03f675c8; // [HIGH] inner worker executed inside protected frame
    constexpr std::uintptr_t resume_finish        = 0x03f676dc; // [HIGH] post‑resume cleanup; copies results back
    constexpr std::uintptr_t lua_resumeerror      = 0x03f67808; // [HIGH] specialized error handler for resumes
    constexpr std::uintptr_t resume_handle        = 0x03f67884; // [HIGH] protected callback for error continuation
    constexpr std::uintptr_t resume_error         = 0x03f67cb0; // [HIGH] pushes error string onto stack, returns LUA_ERRRUN
    constexpr std::uintptr_t lua_yield            = 0x03f67950; // [HIGH] sets status=YIELD, adjusts base to top-nresults
    constexpr std::uintptr_t lua_closethread      = 0x03f6523c; // [HIGH] coroutine.close implementation
    constexpr std::uintptr_t lua_newstate         = 0x03f6f7c8; // [HIGH] (lua_Alloc, void* ud) -> L*; allocates 0x4710 bytes: lua_State at +0, global_State at +0x80
    constexpr std::uintptr_t luaE_newstate        = 0x03f6fac4; // [HIGH] called under protection; inits _G, registry, tmname[], pins error strings, sets GC threshold
    constexpr std::uintptr_t lua_freestate        = 0x03f6fb88; // [HIGH] cleanup and free on init failure
    constexpr std::uintptr_t luaC_threadbarrier   = 0x03f693c0; // [HIGH] called in resume_start after setting isactive=true


    // Memory

    constexpr std::uintptr_t luaM_newobject       = 0x03f6d7e4; // [HIGH] (L, size_t, memcat) -> void*; calls g->frealloc and updates memsize[memcat]


    // Strings

    constexpr std::uintptr_t luaS_hash            = 0x03f6fc34; // [HIGH] (const char*, size_t) -> uint; pure, no side effects
    constexpr std::uintptr_t luaS_newlstr         = 0x03f6ff1c; // [HIGH] (L, const char*, size_t) -> TString*; interns string; new strings get atom=-1, atom2=0x8000
    constexpr std::uintptr_t luaS_resize          = 0x03f6fcc0; // [HIGH] (L, int newsize); rehashes g->strt to newsize buckets


    // Tables

    constexpr std::uintptr_t luaH_new             = 0x03f736b8; // [HIGH] (L, narray, nhash) -> Table*; lsizenode=0, flags=0xFF, node=dummy sentinel
    constexpr std::uintptr_t luaH_resizearray     = 0x03f7376c; // [HIGH] (L, Table*, narray); reallocs Table->array, updates sizearray
    constexpr std::uintptr_t luaH_resizehash      = 0x03f7387c; // [HIGH] (L, Table*, nhash); writes Table->nodemask = ~(-1<<lsizenode) on every resize
    constexpr std::uintptr_t luaH_getstr          = 0x03f73a8c; // [HIGH] (Table*, TString*) -> TValue*; returns nil_tvalue_sentinel on miss, not null


    // Metamethods and type system

    constexpr std::uintptr_t luaT_init            = 0x03f75e60; // [HIGH] interns all 21 tmname[] and 12 ttname[] strings into global_State at startup
    constexpr std::uintptr_t luaT_gettmbyobj      = 0x03f75f6c; // [HIGH] (L, TValue*, uint event) -> TValue*; TABLE/USERDATA use own metatable; everything else falls back to g->mt[tt]


    // GC

    constexpr std::uintptr_t luaC_barrier         = 0x03f6938c; // [HIGH] GC write barrier; checks g->gcstate; prepends obj->gclist to g->gray


    // Misc

    constexpr std::uintptr_t luaO_tostring        = 0x03f7607c; // [HIGH] converts a TValue to its type name string for error messages
    constexpr std::uintptr_t luaopen_vector       = 0x03f76a08; // [HIGH] vector library initializer; not luaT_gettmbyobj, despite what the __index xref count suggests
    constexpr std::uintptr_t vector_index_handler = 0x03f77254; // [HIGH] __index handler registered by luaopen_vector


    // C API

    constexpr std::uintptr_t luaL_register        = 0x03f5e5fc; // [MED]
    constexpr std::uintptr_t lua_pushvector       = 0x03f5bae4; // [HIGH] (float x, float y, float z, lua_State*); L is the last argument on ARM64, not the first
    constexpr std::uintptr_t lua_setfield         = 0x03f5c724; // [MED]
    constexpr std::uintptr_t lua_createtable      = 0x03f5c300; // [MED]  also used as luaL_newlib
    constexpr std::uintptr_t lua_pushcclosure     = 0x03f5bcc4; // [MED]  also used as lua_pushcfunction
    constexpr std::uintptr_t lua_rawseti          = 0x03f5c38c; // [MED]  or a lua_setfield variant


    // Static data
    // These are read-only. Writing to them will ruin your day in creative ways.

    constexpr std::uintptr_t nil_tvalue_sentinel   = 0x04ebdbc0; // [HIGH] returned by luaH_getstr and luaT_gettmbyobj on miss; do not write
    constexpr std::uintptr_t dummy_luanode         = 0x04ebdcd0; // [HIGH] Table->node for empty tables; do not write
    constexpr std::uintptr_t opcode_dispatch_table = 0x05daf138; // [HIGH] ptr[256] x 8 bytes; ~76 non-null entries; byte values are shuffled per build

} // namespace offsets


// lua_State field offsets  (L = lua_State*, sizeof = 0x80)
// lua_State is a GCObject. tt=0x0a=LUA_TTHREAD lives at offset 0.
// Every coroutine has its own lua_State. They share one global_State via l_G.
namespace L
{
    constexpr std::uintptr_t tt            = 0x00; // [HIGH] byte   0x0a = LUA_TTHREAD
    constexpr std::uintptr_t memcat        = 0x01; // [HIGH] byte   GC object memory category
    constexpr std::uintptr_t marked        = 0x02; // [HIGH] byte   GC flags; bit0=color bit3=pinned; value 9 on main thread
    constexpr std::uintptr_t status        = 0x03; // [HIGH] byte   thread status (see status:: namespace below)
    constexpr std::uintptr_t active_memcat = 0x04; // [HIGH] byte   memcat used for objects this thread allocates; distinct from L::memcat which is the thread object's own category
    constexpr std::uintptr_t isactive      = 0x05; // [HIGH] byte   set to 1 when thread is running; used by GC and resume logic
    constexpr std::uintptr_t native_exec   = 0x06; // [HIGH] byte   0x01 = NCG/native code path active; checked by luaV_execute_dispatch
    constexpr std::uintptr_t stacksize     = 0x08; // [HIGH] int    value stack slot count
    constexpr std::uintptr_t size_ci       = 0x0c; // [HIGH] int    CallInfo array slot count; 8 on init
    constexpr std::uintptr_t l_G           = 0x18; // [HIGH] global_State* shared across all coroutines in the same VM
    constexpr std::uintptr_t stack_last    = 0x20; // [HIGH] StkId  one past the last valid stack slot; the overflow boundary
    constexpr std::uintptr_t top           = 0x28; // [HIGH] StkId  current top of stack; next free slot
    constexpr std::uintptr_t stack         = 0x30; // [HIGH] StkId  stack allocation base
    constexpr std::uintptr_t ci            = 0x38; // [HIGH] CallInfo* current active call frame
    constexpr std::uintptr_t base          = 0x40; // [HIGH] StkId  current frame base = &reg[0]; mirrors ci->base; cached for interpreter speed
    constexpr std::uintptr_t end_ci        = 0x58; // [HIGH] CallInfo* one past the last allocated CallInfo slot
    constexpr std::uintptr_t base_ci       = 0x60; // [HIGH] CallInfo* first CallInfo; bottom of the call stack
    constexpr std::uintptr_t nCcalls       = 0x68; // [HIGH] ushort  current C call nesting depth; checked against LUAI_MAXCCALLS (200)
    constexpr std::uintptr_t baseCcalls    = 0x6a; // [HIGH] ushort  C call depth at coroutine entry; used to detect C->Lua->C overflow
    constexpr std::uintptr_t gt            = 0x70; // [HIGH] Table*  _G global environment table
    constexpr std::uintptr_t openupval     = 0x78; // [HIGH] UpVal*  linked list head of open upvalues on this thread's stack
} // namespace L


// CallInfo field offsets  (ci = CallInfo*, sizeof = 0x28)
// Many public references put func at +0x00. That is wrong. It is at +0x18.
// Layout confirmed directly from luaD_precall's store sequence into a fresh CallInfo.
// Note: Roblox reshuffled the order compared to open‑source Luau.
namespace CI
{
    constexpr std::uintptr_t top      = 0x00; // [HIGH] StkId        frame ceiling = func + stacksize * sizeof(TValue)   (NOT numparams)
    constexpr std::uintptr_t savedpc  = 0x08; // [HIGH] Instruction* 0 on C call; set to Proto->code on Lua call; updated by opcode handlers
    constexpr std::uintptr_t base     = 0x10; // [HIGH] StkId        first arg slot / frame base
    constexpr std::uintptr_t func     = 0x18; // [HIGH] StkId        pointer to the function TValue on the value stack; NOT at +0x00
    constexpr std::uintptr_t nresults = 0x20; // [HIGH] int          expected return count; -1 = LUA_MULTRET
    constexpr std::uintptr_t flags    = 0x24; // [HIGH] int          bit0 = LUA_CALLINFO_RETURN, bit1 = LUA_CALLINFO_HANDLE, bit2 = LUA_CALLINFO_NATIVE
} // namespace CI


// TValue field offsets  (sizeof = 0x10)
// The universal Lua value container. tt at +0x0c tells you what the union at +0x00 actually is.
namespace TV
{
    constexpr std::uintptr_t value = 0x00; // [HIGH] 8 bytes  union: GCObject* / double / int / float X+Y for LUA_TVECTOR
    constexpr std::uintptr_t vec_z = 0x08; // [HIGH] float    Z component for LUA_TVECTOR; confirmed via lua_pushvector store sequence
                                           //        lua_pushvector stores: +0x00=X +0x04=Y +0x08=Z +0x0c=tt(5)
                                           //        Zero and unused for all non-vector types.
    constexpr std::uintptr_t tt    = 0x0c; // [HIGH] int      type tag (see types:: namespace)
} // namespace TV


// TString field offsets  (sizeof = 0x18 + len + 1)
// Character data lives inline right after the header. No separate allocation.
namespace TS
{
    constexpr std::uintptr_t tt     = 0x00; // [HIGH] byte    0x06 = LUA_TSTRING
    constexpr std::uintptr_t memcat = 0x01; // [HIGH] byte    GC memory category
    constexpr std::uintptr_t marked = 0x02; // [HIGH] byte    GC flags; bit3=pinned meaning the GC will never touch it
    constexpr std::uintptr_t extra  = 0x03; // [MED]  byte    keyword detection and similar uses (padding in open‑source)
    constexpr std::uintptr_t atom   = 0x04; // [HIGH] int16   fast-comparison ID; -1 (0xFFFF) = not yet assigned
                                            //        luaS_newlstr writes *(uint32*)(s+4) = 0x8000FFFF in one store:
                                            //          atom (+0x04) = 0xFFFF = -1   (unassigned)
                                            //          atom2(+0x06) = 0x8000         (INT16_MIN, unassigned sentinel)
    constexpr std::uintptr_t atom2  = 0x06; // [MED]  int16   second intern index; 0x8000 = unassigned; likely for __namecall or a Roblox-specific string table
    constexpr std::uintptr_t next   = 0x08; // [HIGH] TString* next string in the interning hash chain
    constexpr std::uintptr_t hash   = 0x10; // [HIGH] uint    precomputed hash value; used by luaH_getstr for bucket lookup
    constexpr std::uintptr_t len    = 0x14; // [HIGH] uint    byte length not counting the null terminator
    constexpr std::uintptr_t data   = 0x18; // [HIGH] char[]  inline character data, null terminated
} // namespace TS


// Table field offsets  (sizeof = 0x30)
// Hybrid array/hash. Integer keys 1..sizearray go in the flat array for speed.
// Everything else lands in the hash part (node[]).
namespace TBL
{
    constexpr std::uintptr_t tt        = 0x00; // [HIGH] byte      0x07 = LUA_TTABLE
    constexpr std::uintptr_t memcat    = 0x01; // [HIGH] byte
    constexpr std::uintptr_t marked    = 0x02; // [HIGH] byte      GC color; set to g->currentwhite & 3 on creation
    constexpr std::uintptr_t nodemask  = 0x03; // [HIGH] byte      precomputed hash bucket mask = (1<<lsizenode)-1
                                               //        written by luaH_resizehash as ~(-1<<lsizenode)
                                               //        bucket index = TString->hash & nodemask
    constexpr std::uintptr_t lsizenode = 0x04; // [HIGH] byte      log2 of hash node count; 0 = 1 dummy slot
    constexpr std::uintptr_t flags     = 0x05; // [HIGH] byte      0xFF on init meaning all metamethods are absent
                                               //        bit p = 1 means metamethod p is absent; cleared when a metatable is set
    constexpr std::uintptr_t sizearray = 0x08; // [HIGH] uint      array part capacity in slots
    constexpr std::uintptr_t lastfree  = 0x0c; // [HIGH] uint      free node index stored as an integer, not a pointer unlike standard Lua
                                               //        initialized to 1<<lsizenode; decremented during collision resolution to find free slots
    constexpr std::uintptr_t gclist    = 0x10; // [HIGH] GCObject* GC gray list chain pointer; confirmed by elimination once lastfree was pinned at +0x0c
    constexpr std::uintptr_t array     = 0x18; // [HIGH] TValue*   array part base; NULL on empty table
                                               //        position is +0x18 not +0x10; confirmed by luaH_resizearray realloc sequence
    constexpr std::uintptr_t node      = 0x20; // [HIGH] LuaNode*  hash part; points to dummy_luanode sentinel on empty table
    constexpr std::uintptr_t metatable = 0x28; // [HIGH] Table*    NULL if no metatable is set
} // namespace TBL


// LuaNode field offsets  (sizeof = 0x20)
// Each slot in the Table hash part. Key tag and next-chain offset are packed
// into a single uint32 at +0x1c to save space. Clever until you have to read it.
namespace LN
{
    constexpr std::uintptr_t val_value  = 0x00; // [HIGH] 8 bytes  value union
    constexpr std::uintptr_t val_vec_z  = 0x08; // [HIGH] float    Z component if value is LUA_TVECTOR; zero otherwise
    constexpr std::uintptr_t val_tt     = 0x0c; // [HIGH] int      value type tag
    constexpr std::uintptr_t key_value  = 0x10; // [HIGH] 8 bytes  key union; TString* for string keys
    constexpr std::uintptr_t key_vec_z  = 0x18; // [HIGH] float    Z component if key is LUA_TVECTOR; zero otherwise
    constexpr std::uintptr_t key_packed = 0x1c; // [HIGH] uint32   bits[3:0] = key type tag
                                                //                 bits[31:4] = next offset (signed, in LuaNode units of 0x20 bytes)
                                                //        chain end: key_packed < 0x10 means next=0 and key.tt=nil
} // namespace LN


// Closure field offsets
// Lua and C closures share the same GCObject header. isC at +0x05 is the dividing line.
// Roblox reshuffled stacksize and isC compared to open‑source.
namespace CL
{
    constexpr std::uintptr_t tt          = 0x00; // [HIGH] byte      0x08 = LUA_TFUNCTION
    constexpr std::uintptr_t memcat      = 0x01; // [HIGH] byte
    constexpr std::uintptr_t marked      = 0x02; // [HIGH] byte      GC flags
    constexpr std::uintptr_t stacksize   = 0x03; // [HIGH] byte      max stack slots needed (cached from Proto->maxstacksize)
                                                 //        ci->top = base + stacksize * sizeof(TValue)  (NOT numparams)
    constexpr std::uintptr_t nupvalues   = 0x04; // [MED]  byte      number of upvalues (likely)
    constexpr std::uintptr_t isC         = 0x05; // [HIGH] byte      0 = Lua closure; nonzero = C closure
    constexpr std::uintptr_t unknown_08  = 0x08; // [LOW]  byte      unknown - not gclist; gclist is at +0x10
    constexpr std::uintptr_t gclist      = 0x10; // [HIGH] GCObject* GC gray list chain pointer
                                                 //        luaC_barrier: obj->gclist = g->gray; g->gray = obj
                                                 //        gclist sits at +0x10 for all heap-allocated GC objects
    constexpr std::uintptr_t proto_or_fn = 0x18; // [HIGH] Proto* for Lua closures; lua_CFunction for C closures
    constexpr std::uintptr_t cont        = 0x28; // [HIGH] C closures only; continuation fn ptr
                                                 //        NULL = not yieldable across this call
                                                 //        nonzero = called after a coroutine resumes through this frame
} // namespace CL


// Proto field offsets  (partial)
// The compiled function prototype. All closures from the same function body share one Proto.
// Gap at +0x20 through +0x37 is still dark. Probably sizek, sizecode, nups, nested protos.
namespace PT
{
    constexpr std::uintptr_t tt           = 0x00; // [HIGH] byte      0x0c = LUA_TPROTO
    constexpr std::uintptr_t memcat       = 0x01; // [HIGH] byte
    constexpr std::uintptr_t marked       = 0x02; // [HIGH] byte      GC flags
    constexpr std::uintptr_t numparams    = 0x03; // [HIGH] byte      number of fixed parameters (read from the .luac header)
    constexpr std::uintptr_t is_vararg    = 0x04; // [HIGH] byte      0 = fixed-arg; nonzero = vararg
                                                  //        luaD_precall branches here: if 0, sets L->top = ci->top to cap the stack at numparams slots
    constexpr std::uintptr_t maxstacksize = 0x05; // [HIGH] byte      max register slots needed; used as nil-fill loop bound in luaD_precall
    constexpr std::uintptr_t k            = 0x38; // [HIGH] TValue*   constants array; cached into a register at luaV_execute entry (0x03f7b078: ldr x8,[x8,#0x38])
    constexpr std::uintptr_t code         = 0x40; // [HIGH] Instruction* bytecode array; stored into ci->savedpc at call time
} // namespace PT


// Userdata field offsets  (partial)
namespace UD
{
    constexpr std::uintptr_t metatable = 0x08; // [HIGH] Table* NULL if none; g->mt[LUA_TUSERDATA] is the fallback
} // namespace UD


// global_State field offsets  (g = *(global_State**)(L + L::l_G))
// In the main-thread combined allocation: g = main_thread_ptr + 0x80
// sizeof(global_State) is approximately 0x4690. The full allocation is 0x4710
// with lua_State taking the first 0x80 bytes.
namespace G
{
    constexpr std::uintptr_t GCthreshold    = 0x00;   // [HIGH] size_t       GC threshold; set to totalbytes*4 by luaE_newstate (was nextgc)
    constexpr std::uintptr_t totalbytes     = 0x08;   // [HIGH] size_t       total bytes currently allocated; 0x4710 on init
    constexpr std::uintptr_t frealloc       = 0x10;   // [HIGH] lua_Alloc    allocator function pointer; written by lua_newstate
    constexpr std::uintptr_t ud             = 0x18;   // [HIGH] void*        allocator userdata; written by lua_newstate
    constexpr std::uintptr_t panic          = 0x20;   // [HIGH] lua_CFunction unprotected error handler; NULL on init
                                                      //        not called by luaD_throw since Roblox uses C++ catch instead
    constexpr std::uintptr_t gray           = 0x28;   // [HIGH] GCObject*    active GC gray list head
                                                      //        luaC_barrier prepends: obj->gclist = g->gray; g->gray = obj
                                                      //        zeroed on init; populated during GC marking
    constexpr std::uintptr_t strt_size      = 0x38;   // [HIGH] uint         string interning bucket count; 0x20 after luaS_resize
    constexpr std::uintptr_t strt_nuse      = 0x3c;   // [HIGH] uint         number of interned strings currently alive
    constexpr std::uintptr_t strt_hash      = 0x40;   // [HIGH] TString**    string interning bucket array
    constexpr std::uintptr_t gcgoal         = 0x48;   // [HIGH] uint32       GC goal multiplier; 200 on init (was gcpause)
    constexpr std::uintptr_t gcstepmul      = 0x4c;   // [HIGH] uint32       GC step multiplier; 200 on init
    constexpr std::uintptr_t currentwhite   = 0x54;   // [MED]  byte         current white color; bits[1:0]=mark color; 9 on init (was gccolor)
    constexpr std::uintptr_t gcstate        = 0x55;   // [HIGH] byte         GC phase; 2 = sweep phase which triggers a different barrier path in luaC_barrier
    constexpr std::uintptr_t gc_list_1      = 0x68;   // [MED]  GCObject*    GC traversal list head; initialized to &g+0x58 sentinel on init
                                                      //        not the active gray list (that is g->gray at g+0x28)
                                                      //        probably grayagain or weak; needs luaC_step analysis to confirm
    constexpr std::uintptr_t gc_list_2      = 0x70;   // [MED]  GCObject*    second GC traversal list; same caveat as gc_list_1
    constexpr std::uintptr_t mainthread     = 0x310;  // [HIGH] lua_State*   main thread; set by lua_newstate
    constexpr std::uintptr_t tmname         = 0x320;  // [HIGH] TString*[21] metamethod name strings; see TM:: namespace for indices
    constexpr std::uintptr_t ttname         = 0x3c8;  // [HIGH] TString*[12] type name strings indexed by type tag
    constexpr std::uintptr_t mt             = 0x428;  // [HIGH] Table*[12]   per-type metatables indexed by type tag
                                                      //        mt[6] at g+0x458 is LUA_TSTRING; almost always non-null in Roblox (string library)
                                                      //        mt[7] and mt[9] are fallbacks; TABLE and USERDATA check their own metatable field first
    constexpr std::uintptr_t registry_val   = 0x498;  // [HIGH] Table*       the Lua registry
    constexpr std::uintptr_t registry_tt    = 0x4a4;  // [HIGH] int          always 7 = LUA_TTABLE
    constexpr std::uintptr_t global_set_hook = 0x4b8; // [MED] fn ptr       Roblox sandbox interceptor; checked by the SETGLOBAL opcode handler
    constexpr std::uintptr_t cb_userdata    = 0x540;  // [MED]  void*        lua_Callbacks::userdata
    constexpr std::uintptr_t cb_interrupt   = 0x548;  // [HIGH] fn ptr       lua_Callbacks::interrupt; receives (L, Proto*) when ci->flags bit2 (LUA_CALLINFO_NATIVE) is set
    constexpr std::uintptr_t cb_panic       = 0x550;  // [MED]  fn ptr       lua_Callbacks::panic
    constexpr std::uintptr_t cb_userthread  = 0x558;  // [MED]  fn ptr       lua_Callbacks::userthread
    // Per-memory-category size tracking (confirmed from lua_newstate bzero pattern)
    // g + 0x15c0 = base + 0x1640 (since g = base + 0x80)
    constexpr std::uintptr_t memsize        = 0x15c0; // [HIGH] size_t[256]  per-memcat byte totals
                                                      //        entry 0 = 0x4710 on init (the combined LG block); all others = 0
                                                      //        lua_newstate: bzero(puVar3+0x1644, 0x7f8) then *(size_t*)(puVar3+0x1640) = 0x4710
} // namespace G


// tmname[] indices into g->tmname
// g->tmname + (index * 8) gives you the TString* for that metamethod name
namespace TM
{
    constexpr int INDEX      = 0;  // g+0x320  "__index"
    constexpr int NEWINDEX   = 1;  // g+0x328  "__newindex"
    constexpr int MODE       = 2;  // g+0x330  "__mode"
    constexpr int NAMECALL   = 3;  // g+0x338  "__namecall"  (Luau-specific)
    constexpr int CALL       = 4;  // g+0x340  "__call"
    constexpr int ITER       = 5;  // g+0x348  "__iter"      (Luau-specific)
    constexpr int LEN        = 6;  // g+0x350  "__len"
    constexpr int EQ         = 7;  // g+0x358  "__eq"
    constexpr int ADD        = 8;  // g+0x360  "__add"
    constexpr int SUB        = 9;  // g+0x368  "__sub"
    constexpr int MUL        = 10; // g+0x370  "__mul"
    constexpr int DIV        = 11; // g+0x378  "__div"
    constexpr int IDIV       = 12; // g+0x380  "__idiv"      (Luau-specific)
    constexpr int MOD        = 13; // g+0x388  "__mod"
    constexpr int POW        = 14; // g+0x390  "__pow"
    constexpr int UNM        = 15; // g+0x398  "__unm"
    constexpr int LT         = 16; // g+0x3a0  "__lt"
    constexpr int LE         = 17; // g+0x3a8  "__le"
    constexpr int CONCAT     = 18; // g+0x3b0  "__concat"
    constexpr int TYPE       = 19; // g+0x3b8  "__type"      (Luau-specific)
    constexpr int METATABLE  = 20; // g+0x3c0  "__metatable"
} // namespace TM


// Type tags
namespace types
{
    constexpr int Nil           = 0x00;
    constexpr int Boolean       = 0x01;
    constexpr int LightUserdata = 0x02;
    constexpr int Number        = 0x03; // double
    constexpr int Integer       = 0x04; // int64; Luau-specific, distinct from Number
    constexpr int Vector        = 0x05; // 3-component float; Luau-specific; Z stored at TV::vec_z
    constexpr int String        = 0x06;
    constexpr int Table         = 0x07;
    constexpr int Function      = 0x08;
    constexpr int Userdata      = 0x09;
    constexpr int Thread        = 0x0a;
    constexpr int Buffer        = 0x0b; // typed byte buffer; Luau-specific
    constexpr int Proto         = 0x0c; // internal - used by Proto objects
} // namespace types


// Error codes passed to luaD_throw and stored in lua_exception::errcode
namespace errcodes
{
    constexpr int ERRRUN    = 0x02; // runtime error
    constexpr int ERRSYNTAX = 0x03; // syntax error
    constexpr int ERRMEM    = 0x04; // memory allocation failure; the fun kind
    constexpr int ERRERR    = 0x05; // error while handling an error; yes that's a thing
} // namespace errcodes


// Thread status values for L::status
namespace status
{
    constexpr int OK      = 0x00; // suspended / resumable
    constexpr int Yield   = 0x01; // LUA_YIELD; parked, waiting to be resumed
    constexpr int Break   = 0x06; // LUA_BREAK; Luau-specific debug break
    constexpr int Running = 0x7f; // sentinel meaning the thread is already running; do not resume it
} // namespace status


// lua_exception object layout  (sizeof = 0x18)
// Roblox uses C++ exceptions instead of setjmp/longjmp.
// There are no setjmp buffers in lua_State. Stop looking for them.
// luaD_throw calls __cxa_allocate_exception(0x18) then __cxa_throw.
// lua_resume catches it via a normal C++ try/catch block.
namespace lua_exception
{
    constexpr std::uintptr_t vtable  = 0x00; // vtable pointer
    constexpr std::uintptr_t state   = 0x08; // lua_State* the thread that threw
    constexpr std::uintptr_t errcode = 0x10; // int LUA_ERR* code

    constexpr std::uintptr_t typeinfo_addr = 0x05dadd60; // lua_exception typeinfo object
    constexpr std::uintptr_t vtable_addr   = 0x05dadd88; // lua_exception vtable
} // namespace lua_exception
