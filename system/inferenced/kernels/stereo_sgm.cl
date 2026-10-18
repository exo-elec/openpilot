/* Four independent SGM paths, census 5x5, GPU-only execution.
 * Integer costs; OpenCL 1.2; no float atomics or relaxed finite math.
 * D is a power of two <=128. Each path group owns one scanline and each
 * lane owns one disparity. Path launches are serialized by an in-order queue.
 */
__kernel void census5(__global const uchar *image, __global uint *out, int w, int h) {
  int i=get_global_id(0); if(i>=w*h) return;
  int x=i%w, y=i/w; uint bits=0;
  if(x>=2 && x<w-2 && y>=2 && y<h-2) {
    uchar center=image[i];
    for(int dy=-2;dy<=2;dy++) for(int dx=-2;dx<=2;dx++) {
      if(dx==0 && dy==0) continue;
      bits=(bits<<1) | (uint)(image[(y+dy)*w+x+dx]<center);
    }
  }
  out[i]=bits;
}

__kernel void matching_cost(__global const uint *a, __global const uint *b,
                            __global uchar *cost, int w, int h, int D, int direction) {
  int i=get_global_id(0); if(i>=w*h*D) return;
  int d=i%D, pixel=i/D, x=pixel%w, y=pixel/w, rx=x+direction*d;
  cost[i]=(x<2 || x>=w-2 || y<2 || y>=h-2 || rx<2 || rx>=w-2)
          ? (uchar)63 : (uchar)popcount(a[pixel]^b[y*w+rx]);
}

__kernel void aggregate_path(__global const uchar *cost, __global ushort *sum,
                             int w, int h, int D, int p1, int p2, int path) {
  int d=get_local_id(0), line=get_group_id(0);
  __local int prev[128], next[128], minimum[128];
  prev[d]=0; barrier(CLK_LOCAL_MEM_FENCE);
  int length=path<2?w:h;
  for(int step=0;step<length;step++) {
    minimum[d]=prev[d]; barrier(CLK_LOCAL_MEM_FENCE);
    for(int offset=D/2;offset>0;offset/=2) {
      if(d<offset) minimum[d]=min(minimum[d],minimum[d+offset]);
      barrier(CLK_LOCAL_MEM_FENCE);
    }
    int x=path<2?(path==0?step:w-1-step):line;
    int y=path<2?line:(path==2?step:h-1-step);
    int index=(y*w+x)*D+d;
    int smooth=min(prev[d],minimum[0]+p2);
    if(d>0) smooth=min(smooth,prev[d-1]+p1);
    if(d<D-1) smooth=min(smooth,prev[d+1]+p1);
    next[d]=(int)cost[index]+smooth-minimum[0];
    sum[index]=(ushort)((path==0?0:(int)sum[index])+next[d]);
    barrier(CLK_LOCAL_MEM_FENCE);
    prev[d]=next[d]; barrier(CLK_LOCAL_MEM_FENCE);
  }
}

__kernel void select_disparity(__global const ushort *sum, __global float *disp,
                               __global float *confidence, int w, int h, int D,
                               int direction, int subpixel) {
  int i=get_global_id(0); if(i>=w*h) return;
  int x=i%w, y=i/w, start=i*D, best=0, bestcost=65535, second=65535;
  for(int d=0;d<D;d++) if((int)sum[start+d]<bestcost) { best=d;bestcost=sum[start+d]; }
  for(int d=0;d<D;d++) if(abs(d-best)>1) second=min(second,(int)sum[start+d]);
  float certainty=second==65535?0.0f:(float)(second-bestcost)/(float)max(second,1);
  float value=(float)best;
  if(subpixel && best>0 && best<D-1) {
    float a=sum[start+best-1], b=bestcost, c=sum[start+best+1];
    float denom=2.0f*(a+c-2.0f*b);
    if(denom>0.0f) value+=clamp((a-c)/denom,-0.5f,0.5f);
  }
  int rx=x+direction*best;
  int valid=x>=2 && x<w-2 && y>=2 && y<h-2 && rx>=2 && rx<w-2 && best>0 && certainty>0.05f;
  disp[i]=valid?value:0.0f;
  confidence[i]=valid?clamp(certainty,0.0f,1.0f):0.0f;
}

__kernel void median3(__global const float *input, __global float *output, int w, int h) {
  int i=get_global_id(0); if(i>=w*h) return;
  int x=i%w,y=i/w;
  if(input[i]<=0.0f || x<1 || x>=w-1 || y<1 || y>=h-1) {output[i]=input[i];return;}
  float values[9];int n=0;
  for(int dy=-1;dy<=1;dy++) for(int dx=-1;dx<=1;dx++) {
    float value=input[(y+dy)*w+x+dx];
    if(value>0.0f) {int j=n;while(j>0 && values[j-1]>value) {values[j]=values[j-1];j--;}values[j]=value;n++;}
  }
  output[i]=n>=5?values[n/2]:input[i];
}

__kernel void consistency(__global const float *left, __global const float *right,
                           __global const float *lc, __global const float *rc,
                           __global float *disparity, __global float *confidence,
                           int w, int h) {
  int i=get_global_id(0); if(i>=w*h) return;
  float d=left[i];int x=i%w,y=i/w,rx=(int)floor((float)x-d+0.5f);
  float score=0.0f;
  if(d>0.0f && rx>=2 && rx<w-2) {
    int ri=y*w+rx;
    if(right[ri]>0.0f && fabs(d-right[ri])<=1.0f) score=min(lc[i],rc[ri]);
  }
  disparity[i]=score>0.05f?d:0.0f;
  confidence[i]=score>0.05f?score:0.0f;
}
