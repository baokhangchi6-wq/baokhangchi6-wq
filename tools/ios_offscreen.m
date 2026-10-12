#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <math.h>

/* Real render-target textures. A slot is reusable only after the job and all its buffers finish. */
@interface MSRenderPool : NSObject
@property NSUInteger width, height, format, busy;
@property double due;
@property(strong) NSMutableArray *textures;
@property(strong) MSRenderPool *retired;
@end
@implementation MSRenderPool
- (instancetype)init { if ((self=[super init])) _textures=[NSMutableArray arrayWithObjects:NSNull.null,NSNull.null,NSNull.null,nil]; return self; }
@end
@interface MSRenderTicket : NSObject
@property(strong) MSRenderPool *pool;
@property(strong) id texture;
@property(weak) id layer;
@property NSInteger slot;
@property NSUInteger pending, epoch;
@property BOOL done, offscreen, submitted, bound, encoded;
@end
@implementation MSRenderTicket @end
static NSMapTable *MSPools, *MSActiveTickets;
static NSObject *MSPoolLock;
static NSUInteger MSEpoch;
static uint64_t MSCounts[6]; // offscreen selected, real selected, pool busy, allocation failed, completed offscreen, completion unavailable
static _Thread_local void *MSTicket;
static BOOL MSCompletionReady;
static char MSBufferTicketKey;
static double ms_offscreen_time(void) { return NSProcessInfo.processInfo.systemUptime; }
static void ms_pool_prepare(void) { static dispatch_once_t once; dispatch_once(&once,^{ MSPoolLock=[NSObject new]; MSPools=[NSMapTable weakToStrongObjectsMapTable];MSActiveTickets=[NSMapTable weakToStrongObjectsMapTable]; }); }
static void ms_ticket_finish(MSRenderTicket *ticket) {
    if (!ticket.done || ticket.pending || (ticket.offscreen && ticket.bound && !ticket.submitted)) return;
    if (ticket.slot>=0) ticket.pool.busy &= ~(1UL<<ticket.slot);
    if (ticket.offscreen && ticket.submitted && ticket.encoded && ticket.epoch==MSEpoch) MSCounts[4]++;
    if(ticket.bound && ticket.submitted) ticket.pool.retired=nil;
    if (ticket.texture && [MSActiveTickets objectForKey:ticket.texture]==ticket) [MSActiveTickets removeObjectForKey:ticket.texture];
    ticket.slot=-1; ticket.offscreen=NO;
}
void ms_offscreen_reset(unsigned epoch) {
    ms_pool_prepare(); @synchronized(MSPoolLock) { MSEpoch=epoch; memset(MSCounts,0,sizeof(MSCounts)); }
}
void ms_offscreen_completion_ready(void *pointer) {
    ms_pool_prepare(); Class cls=(__bridge Class)pointer;
    Method method=class_getInstanceMethod(cls,@selector(addCompletedHandler:));
    char *type=method ? method_copyArgumentType(method,2) : NULL;
    BOOL valid=method && method_getNumberOfArguments(method)==3 && type && strcmp(type,"@?")==0;
    free(type);
    if(valid) @synchronized(MSPoolLock) { MSCompletionReady=YES; }
}
void ms_offscreen_mark_layer(void *pointer) {
    ms_pool_prepare(); id layer=(__bridge id)pointer;
    if (!layer || ![layer respondsToSelector:@selector(device)] || ![layer respondsToSelector:@selector(drawableSize)] || ![layer respondsToSelector:@selector(pixelFormat)]) return;
    @synchronized(MSPoolLock) { if (![MSPools objectForKey:layer] && MSPools.count==0) [MSPools setObject:[MSRenderPool new] forKey:layer]; }
}
void ms_offscreen_job_end(void) {
    MSRenderTicket *ticket=MSTicket ? CFBridgingRelease(MSTicket) : nil; MSTicket=NULL;
    if (ticket) @synchronized(MSPoolLock) {
        ticket.done=YES;
        if(ticket.offscreen && ticket.bound && !ticket.submitted) { MSCounts[5]++;MSCompletionReady=NO; }
        ms_ticket_finish(ticket);
    }
}
void ms_offscreen_job_begin(void) { ms_offscreen_job_end(); }
void *ms_offscreen_acquire(void *pointer,double now,int displayHz,int requested) {
    ms_pool_prepare(); id layer=(__bridge id)pointer;
    if(!layer) return NULL;
    MSRenderTicket *current=(__bridge MSRenderTicket*)MSTicket;
    if (current.offscreen && current.layer==layer) return NULL;
    MSRenderPool *pool; BOOL eligible;
    @synchronized(MSPoolLock) { pool=[MSPools objectForKey:layer]; eligible=MSEpoch && pool && MSCompletionReady && requested>displayHz && displayHz>=30 && displayHz<=240 && now<pool.due && !current; }
    if (eligible) {
        CGSize size=((CGSize (*)(id,SEL))objc_msgSend)(layer,@selector(drawableSize));
        NSUInteger format=((NSUInteger (*)(id,SEL))objc_msgSend)(layer,@selector(pixelFormat));
        id<MTLDevice> device=((id (*)(id,SEL))objc_msgSend)(layer,@selector(device));
        @synchronized(MSPoolLock) {
            BOOL colorFormat=format==MTLPixelFormatBGRA8Unorm || format==MTLPixelFormatBGRA8Unorm_sRGB || format==MTLPixelFormatRGBA8Unorm || format==MTLPixelFormatRGBA8Unorm_sRGB;
            if (device && colorFormat && isfinite(size.width) && isfinite(size.height) && size.width>=64 && size.height>=64 && size.width<=8192 && size.height<=8192 && size.width*size.height<=16000000) {
                if (pool.width!=(NSUInteger)size.width || pool.height!=(NSUInteger)size.height || pool.format!=format) {
                    if(pool.busy) { MSCounts[2]++; goto real_drawable; }
                    MSRenderPool *old=pool; pool=[MSRenderPool new];pool.retired=old;
                    pool.width=(NSUInteger)size.width;pool.height=(NSUInteger)size.height;pool.format=format;pool.due=old.due;
                    [MSPools setObject:pool forKey:layer];
                }
                for (NSInteger slot=0;slot<3;slot++) {
                    if (pool.busy&(1UL<<slot)) continue;
                    id texture=pool.textures[slot];
                    if (texture==NSNull.null) {
                        MTLTextureDescriptor *descriptor=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:pool.width height:pool.height mipmapped:NO];
                        descriptor.storageMode=MTLStorageModePrivate;descriptor.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
                        texture=[device newTextureWithDescriptor:descriptor];
                        if (!texture) { MSCounts[3]++;break; }
                        pool.textures[slot]=texture;
                    }
                    MSRenderTicket *ticket=[MSRenderTicket new];ticket.pool=pool;ticket.texture=texture;ticket.layer=layer;
                    ticket.slot=slot;ticket.offscreen=YES;ticket.epoch=MSEpoch;
                    pool.busy|=1UL<<slot;MSTicket=(__bridge_retained void*)ticket;MSCounts[0]++;
                    [MSActiveTickets setObject:ticket forKey:texture];
                    return NULL; // legitimate offscreen frame; the guarded engine bind uses ticket.texture
                }
                if (pool.busy==7) MSCounts[2]++;
            } else MSCounts[3]++;
        }
    }
real_drawable:;
    id drawable=((id (*)(id,SEL))objc_msgSend)(layer,@selector(nextDrawable));
    @synchronized(MSPoolLock) {
        /* ponytail: half a display period for extra real renders; adapt after GPU/present receipts prove a better deadline. */
        if (pool) pool.due=ms_offscreen_time()+0.5/(displayHz>0 ? displayHz : 60);
        MSCounts[1]++;
        if (pool && drawable && !MSTicket) {
            MSRenderTicket *ticket=[MSRenderTicket new];ticket.pool=pool;ticket.layer=layer;ticket.slot=-1;ticket.epoch=MSEpoch;
            MSTicket=(__bridge_retained void*)ticket;
        }
    }
    return (__bridge void*)drawable;
}
void *ms_offscreen_texture(void *pointer) {
    id drawable=(__bridge id)pointer;MSRenderTicket *ticket=(__bridge MSRenderTicket*)MSTicket;
    id texture=drawable ? ((id (*)(id,SEL))objc_msgSend)(drawable,@selector(texture)) : ticket.offscreen ? ticket.texture : nil;
    if (ticket && texture) { ticket.texture=texture;ticket.bound=YES; }
    return (__bridge void*)texture;
}
int ms_offscreen_allows_null(void *pointer) {
    if(pointer) return 1;
    MSRenderTicket *ticket=(__bridge MSRenderTicket*)MSTicket;
    return ticket.offscreen && ticket.texture ? 1 : 0;
}
void ms_offscreen_observe_encoder(void *bufferPointer,void *descriptorPointer) {
    ms_pool_prepare();id buffer=(__bridge id)bufferPointer;
    MTLRenderPassDescriptor *descriptor=(__bridge id)descriptorPointer;
    if(!descriptor || ![descriptor respondsToSelector:@selector(colorAttachments)]) return;
    id texture=descriptor.colorAttachments[0].texture;
    if(!texture) return;
    @synchronized(MSPoolLock) {
        MSRenderTicket *ticket=[MSActiveTickets objectForKey:texture];
        if(ticket) ticket.encoded=YES;
        if(ticket && !objc_getAssociatedObject(buffer,&MSBufferTicketKey)) {
            ticket.pending++;
            objc_setAssociatedObject(buffer,&MSBufferTicketKey,ticket,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        }
    }
}
void ms_offscreen_bind_buffer(void *pointer) {
    MSRenderTicket *ticket=(__bridge MSRenderTicket*)MSTicket;
    id buffer=(__bridge id)pointer;
    if(!ticket || !buffer || ![buffer respondsToSelector:@selector(addCompletedHandler:)]) return;
    @synchronized(MSPoolLock) {
        if(!objc_getAssociatedObject(buffer,&MSBufferTicketKey)) {
            ticket.pending++;
            objc_setAssociatedObject(buffer,&MSBufferTicketKey,ticket,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        }
    }
}
void ms_offscreen_commit(void *pointer) {
    id<MTLCommandBuffer> buffer=(__bridge id)pointer;
    MSRenderTicket *ticket=objc_getAssociatedObject(buffer,&MSBufferTicketKey);
    if (!ticket) return;
    if (![buffer respondsToSelector:@selector(addCompletedHandler:)]) { @synchronized(MSPoolLock) { MSCounts[5]++; } return; }
    @synchronized(MSPoolLock) { ticket.submitted=YES; }
    [buffer addCompletedHandler:^(id<MTLCommandBuffer> completed) {
        @synchronized(MSPoolLock) { if (ticket.pending) ticket.pending--;ms_ticket_finish(ticket); }
    }];
}
void ms_offscreen_counts(uint64_t *out) { ms_pool_prepare(); @synchronized(MSPoolLock) { for(int i=0;i<6;i++) out[i]=MSCounts[i]; } }
