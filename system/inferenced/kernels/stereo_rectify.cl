/* Bilinear remap of measured factory maps. Unsupported borders are zero;
 * StereoD's census-footprint support mask rejects their correspondences.
 */
__kernel void rectify_luma(__global const uchar *image, __global const float *mx,
                            __global const float *my, __global uchar *output,
                            int width, int height) {
  int i=get_global_id(0); if(i>=width*height) return;
  float x=mx[i], y=my[i]; uchar value=0;
  if(isfinite(x) && isfinite(y) && x>=0 && y>=0 && x<width-1 && y<height-1) {
    int u=(int)floor(x), v=(int)floor(y);
    float a=x-u, b=y-v;
    float top=mix((float)image[v*width+u],(float)image[v*width+u+1],a);
    float bottom=mix((float)image[(v+1)*width+u],(float)image[(v+1)*width+u+1],a);
    value=convert_uchar_sat_rte(mix(top,bottom,b));
  }
  output[i]=value;
}
