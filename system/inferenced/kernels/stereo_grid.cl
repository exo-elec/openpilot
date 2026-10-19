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
