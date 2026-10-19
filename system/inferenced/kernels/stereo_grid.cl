/* Conservative stereo endpoint observations, not a LiDAR ray-tracer.
 * Vehicle coordinates: forward, left, up. Unknown is never written free.
 * Classification and rasterization stay on the GPU.
 */
__kernel void classify_stereo(__global const float *optical,
                              __constant float *transform, __constant float *plane,
                              __global float *aligned, __global int *labels,
                              volatile __global int *nearest, int width, int count) {
  int i=get_global_id(0); if(i>=count) return;
  float3 p=vload3(i,optical), q=(float3)(NAN,NAN,NAN);
  int label=0;
  if(all(isfinite(p))) {
    float4 h=(float4)(p,1.0f);
    float3 v=(float3)(dot(vload4(0,transform),h),dot(vload4(1,transform),h),dot(vload4(2,transform),h));
    float height=dot(vload4(0,plane),(float4)(v,1.0f));
    if(all(isfinite(v)) && v.x>0.5f && v.x<55.0f && fabs(v.y)<15.0f) {
      q=(float3)(-v.y,-v.z,v.x); /* existing gridd right/down/forward convention */
      if(fabs(height)<=0.10f) label=1;
      else if(height>=0.18f && height<=2.5f) {
        label=2;
        atomic_min(nearest+i%width,(int)(p.z*1000.0f));
      }
    }
  }
  vstore3(q,i,aligned); labels[i]=label;
}

__kernel void raster_stereo(__global const float *optical, __global const float *aligned,
                            __global const int *labels, __global const int *nearest,
                            volatile __global int *cells, volatile __global int *representatives,
                            int width, int count,
                            float resolution, float origin_forward, float origin_left,
                            int rows, int cols) {
  int i=get_global_id(0); if(i>=count) return;
  int label=labels[i]; if(label==0) return;
  /* A vertical obstacle shadows the rest of its image column. This deliberately
   * loses some ground evidence rather than clearing space behind an obstacle.
   */
  if(label==1 && vload3(i,optical).z*1000.0f>nearest[i%width]-500) return;
  float3 p=vload3(i,aligned);
  int r=(int)floor((p.z-origin_forward)/resolution);
  int c=(int)floor((-p.x-origin_left)/resolution);
  if(r>=0 && r<rows && c>=0 && c<cols) {
    int cell=r*cols+c;
    atomic_or(cells+cell,label);
    if(label==2) atomic_cmpxchg(representatives+cell,-1,i);
  }
}

/* At most one obstacle sample per cell reaches CPU object-range fusion. */
__kernel void obstacle_cell_points(__global const float *aligned, __global const int *representatives,
                                    __global float *points, int count) {
  int cell=get_global_id(0); if(cell>=count) return;
  int i=representatives[cell];
  float3 p=(float3)(NAN,NAN,NAN);
  if(i>=0) p=vload3(i,aligned);
  vstore3(p,cell,points);
}

/* Project each ground-cell footprint back into the calibrated image. The four
 * corners bound its perspective projection on the locally measured plane.
 * Any overlap with a nearer obstacle column makes this cell unknown, preventing
 * segmentation from painting free road through a stereo occlusion.
 */
__kernel void stereo_shadow_cells(__constant float *geometry, __constant float *plane,
                                   __global const int *nearest, volatile __global int *cells,
                                   int width, int height, float resolution,
                                   float origin_forward, float origin_left, int rows, int cols) {
  int cell=get_global_id(0); if(cell>=rows*cols) return;
  int row=cell/cols, col=cell%cols;
  float x0=origin_forward+row*resolution, y0=origin_left+col*resolution;
  if(x0<0.5f) return;
  float umin=INFINITY, umax=-INFINITY, vmin=INFINITY, vmax=-INFINITY, depth=INFINITY;
  for(int corner=0;corner<4;corner++) {
    float x=x0+((corner&1)?resolution:0.0f);
    float y=y0+((corner&2)?resolution:0.0f);
    float z=-(plane[0]*x+plane[1]*y+plane[3])/plane[2];
    float4 p=(float4)(x,y,z,1.0f);
    float3 optical=(float3)(dot(vload4(4,geometry),p),dot(vload4(5,geometry),p),dot(vload4(6,geometry),p));
    if(!all(isfinite(optical)) || optical.z<=0.1f) return;
    float4 h=(float4)(optical,1.0f);
    float4 image=(float4)(dot(vload4(8,geometry),h),dot(vload4(9,geometry),h),
                          dot(vload4(10,geometry),h),dot(vload4(11,geometry),h));
    if(!all(isfinite(image)) || fabs(image.w)<1.0e-20f) return;
    float u=image.x/image.w, v=image.y/image.w;
    umin=fmin(umin,u); umax=fmax(umax,u);
    vmin=fmin(vmin,v); vmax=fmax(vmax,v);
    depth=fmin(depth,optical.z);
  }
  if(umax<0 || umin>=width || vmax<0 || vmin>=height) return;
  int begin=clamp((int)floor(umin),0,width-1), end=clamp((int)ceil(umax),0,width-1);
  for(int u=begin;u<=end;u++) {
    if(depth*1000.0f>nearest[u]-500) { atomic_or(cells+cell,4); return; }
  }
}
