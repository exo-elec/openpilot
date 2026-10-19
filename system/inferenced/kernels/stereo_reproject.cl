/* Optical-frame projection of raw pixel disparity through complete Q.
 * Geometry contract: ROS image_geometry/StereoCameraModel and OpenCV
 * reprojectImageTo3D. Invalid samples stay NaN, never a point at the origin.
 */
__kernel void stereo_reproject(__global const float *disparity,
                               __global const float *confidence,
                               __constant float *q, __global float *xyz,
                               int width, int height, float min_confidence,
                               float min_depth, float max_depth) {
  int i=get_global_id(0); if(i>=width*height) return;
  float d=disparity[i], c=confidence[i];
  float3 point=(float3)(NAN,NAN,NAN);
  if(isfinite(d) && d>0.0f && isfinite(c) && c>min_confidence && c<=1.0f) {
    float4 pixel=(float4)((float)(i%width),(float)(i/width),d,1.0f);
    float4 p=(float4)(dot(vload4(0,q),pixel),dot(vload4(1,q),pixel),
                      dot(vload4(2,q),pixel),dot(vload4(3,q),pixel));
    if(all(isfinite(p)) && fabs(p.w)>1.0e-20f) {
      float3 projected=p.xyz/p.w;
      if(all(isfinite(projected)) && projected.z>min_depth && projected.z<max_depth) {
        float4 h=(float4)(projected,1.0f);
        float3 transformed=(float3)(dot(vload4(4,q),h),dot(vload4(5,q),h),dot(vload4(6,q),h));
        if(all(isfinite(transformed))) point=transformed;
      }
    }
  }
  vstore3(point,i,xyz);
}
