"""Verify real texture pool ownership with controlled Metal completion fixtures."""
import subprocess
import sys
import tempfile
from pathlib import Path

def main():
    if sys.platform!='darwin':raise SystemExit('Offscreen ownership test requires macOS Metal/Foundation')
    source=(Path(__file__).parent/'ios_offscreen.m').read_text(encoding='utf-8')+r'''
#include <assert.h>
#include <float.h>
@interface MSFixtureTexture:NSObject @end
@implementation MSFixtureTexture @end
@interface MSFixtureDevice:NSObject
- (id)newTextureWithDescriptor:(MTLTextureDescriptor*)descriptor;
@end
@implementation MSFixtureDevice
- (id)newTextureWithDescriptor:(MTLTextureDescriptor*)descriptor {
    assert(descriptor.storageMode==MTLStorageModePrivate);
    assert(descriptor.usage==(MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead));
    return [MSFixtureTexture new];
}
@end
@interface MSFixtureDrawable:NSObject @property(strong) id texture; @end
@implementation MSFixtureDrawable @end
@interface MSFixtureLayer:NSObject
@property(strong) id device;
@property CGSize drawableSize;
@property NSUInteger pixelFormat, calls;
- (id)nextDrawable;
@end
@implementation MSFixtureLayer
- (id)nextDrawable { self.calls++;MSFixtureDrawable* value=[MSFixtureDrawable new];value.texture=[MSFixtureTexture new];return value; }
@end
@interface MSFixtureBuffer:NSObject
@property(copy) void (^completion)(id);
- (void)addCompletedHandler:(void (^)(id))handler;
- (void)finish;
@end
@implementation MSFixtureBuffer
- (void)addCompletedHandler:(void (^)(id))handler {self.completion=handler;}
- (void)finish { if(self.completion) self.completion(self);self.completion=nil; }
@end
@interface MSFixtureAttachment:NSObject @property(strong) id texture; @end
@implementation MSFixtureAttachment @end
@interface MSFixtureAttachments:NSObject
@property(strong) id attachment;
- (id)objectAtIndexedSubscript:(NSUInteger)index;
@end
@implementation MSFixtureAttachments
- (id)objectAtIndexedSubscript:(NSUInteger)index {return self.attachment;}
@end
@interface MSFixturePass:NSObject @property(strong) id colorAttachments; @end
@implementation MSFixturePass @end
static MSFixtureBuffer* encode(id texture) {
    MSFixtureAttachment* attachment=[MSFixtureAttachment new];attachment.texture=texture;
    MSFixtureAttachments* attachments=[MSFixtureAttachments new];attachments.attachment=attachment;
    MSFixturePass* pass=[MSFixturePass new];pass.colorAttachments=attachments;
    MSFixtureBuffer* buffer=[MSFixtureBuffer new];
    ms_offscreen_observe_encoder((__bridge void*)buffer,(__bridge void*)pass);
    ms_offscreen_commit((__bridge void*)buffer);
    return buffer;
}
int main(void) { @autoreleasepool {
    MSFixtureLayer* layer=[MSFixtureLayer new];layer.device=[MSFixtureDevice new];layer.drawableSize=CGSizeMake(1398,1290);layer.pixelFormat=80;
    ms_offscreen_reset(1);ms_offscreen_mark_layer((__bridge void*)layer);
    ms_offscreen_job_begin();
    void* real=ms_offscreen_acquire((__bridge void*)layer,ms_offscreen_time(),120,2000);
    assert(real && layer.calls==1);assert(ms_offscreen_allows_null(real));ms_offscreen_job_end();
    ms_offscreen_completion_ready((__bridge void*)[MSFixtureBuffer class]);
    NSMutableArray* buffers=[NSMutableArray array];NSMutableArray* textures=[NSMutableArray array];
    for(int i=0;i<3;i++) {
        ((MSRenderPool*)[MSPools objectForKey:layer]).due=DBL_MAX;
        ms_offscreen_job_begin();
        assert(!ms_offscreen_acquire((__bridge void*)layer,ms_offscreen_time(),120,2000));
        assert(ms_offscreen_allows_null(NULL));
        id texture=(__bridge id)ms_offscreen_texture(NULL);assert(texture);[textures addObject:texture];
        MSFixtureBuffer* buffer=encode(texture);[buffers addObject:buffer];
        if(i==0) { [buffer finish];assert(((MSRenderPool*)[MSPools objectForKey:layer]).busy==1); }
        ms_offscreen_job_end();
        if(i==0) assert(((MSRenderPool*)[MSPools objectForKey:layer]).busy==0);
    }
    assert(textures[0]==textures[1]); // completion plus job-end makes slot reusable
    ((MSRenderPool*)[MSPools objectForKey:layer]).due=DBL_MAX;
    ms_offscreen_job_begin();
    assert(!ms_offscreen_acquire((__bridge void*)layer,ms_offscreen_time(),120,2000));
    id third=(__bridge id)ms_offscreen_texture(NULL);[buffers addObject:encode(third)];ms_offscreen_job_end();
    assert(((MSRenderPool*)[MSPools objectForKey:layer]).busy==7);
    ((MSRenderPool*)[MSPools objectForKey:layer]).due=DBL_MAX;
    ms_offscreen_job_begin();assert(ms_offscreen_acquire((__bridge void*)layer,ms_offscreen_time(),120,2000));ms_offscreen_job_end();
    layer.drawableSize=CGSizeMake(2796,1290);
    ((MSRenderPool*)[MSPools objectForKey:layer]).due=DBL_MAX;
    ms_offscreen_job_begin();assert(ms_offscreen_acquire((__bridge void*)layer,ms_offscreen_time(),120,2000));ms_offscreen_job_end();
    ms_offscreen_reset(2);
    for(MSFixtureBuffer* buffer in buffers) [buffer finish];
    assert(((MSRenderPool*)[MSPools objectForKey:layer]).busy==0);
    uint64_t counts[6];ms_offscreen_counts(counts);assert(counts[4]==0);
    ms_offscreen_reset(0);
    ((MSRenderPool*)[MSPools objectForKey:layer]).due=DBL_MAX;
    ms_offscreen_job_begin();assert(ms_offscreen_acquire((__bridge void*)layer,ms_offscreen_time(),120,2000));ms_offscreen_job_end();
    assert(!ms_offscreen_allows_null(NULL));
    id<MTLDevice> gpu=MTLCreateSystemDefaultDevice();
    if(gpu) {
        @synchronized(MSPoolLock) { MSPools=[NSMapTable weakToStrongObjectsMapTable]; }
        layer.device=gpu;layer.drawableSize=CGSizeMake(64,64);
        ms_offscreen_reset(3);ms_offscreen_mark_layer((__bridge void*)layer);
        id<MTLCommandQueue> queue=[gpu newCommandQueue];
        id<MTLCommandBuffer> buffer=[queue commandBuffer];
        ms_offscreen_completion_ready((__bridge void*)[buffer class]);
        ((MSRenderPool*)[MSPools objectForKey:layer]).due=DBL_MAX;
        ms_offscreen_job_begin();
        assert(!ms_offscreen_acquire((__bridge void*)layer,ms_offscreen_time(),120,2000));
        id<MTLTexture> texture=(__bridge id)ms_offscreen_texture(NULL);
        assert(texture.width==64 && texture.height==64 && texture.pixelFormat==80);
        MTLRenderPassDescriptor* pass=[MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture=texture;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLRenderCommandEncoder> encoder=[buffer renderCommandEncoderWithDescriptor:pass];assert(encoder);
        ms_offscreen_bind_buffer((__bridge void*)buffer);
        ms_offscreen_observe_encoder((__bridge void*)buffer,(__bridge void*)pass);
        [encoder endEncoding];ms_offscreen_commit((__bridge void*)buffer);ms_offscreen_job_end();
        assert(((MSRenderPool*)[MSPools objectForKey:layer]).busy==1);
        [buffer commit];[buffer waitUntilCompleted];
        for(int i=0;i<100 && ((MSRenderPool*)[MSPools objectForKey:layer]).busy;i++) [NSThread sleepForTimeInterval:0.001];
        assert(buffer.status==MTLCommandBufferStatusCompleted);
        assert(((MSRenderPool*)[MSPools objectForKey:layer]).busy==0);
        ms_offscreen_counts(counts);assert(counts[4]==1);
        puts("PASS real Metal render target and GPU completion ownership");
    } else puts("Real Metal device unavailable; controlled ownership fixture passed");
} return 0; }
'''
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);(root/'check.m').write_text(source,encoding='utf-8')
        subprocess.run(['xcrun','clang','-fobjc-arc','-O0',str(root/'check.m'),'-framework','Foundation','-framework','Metal','-o',str(root/'check')],check=True)
        subprocess.run([str(root/'check')],check=True)
    print('PASS offscreen pool: completion + job ownership, resize/busy fallback, epoch reset and native baseline')
if __name__=='__main__':main()
