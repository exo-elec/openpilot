# Third-party notices

NPBM and this package are NavPilot's own protocol and code. They follow the design lessons, constants and test ideas of
[Jetlink](https://github.com/zoompilot/jetlink) (MIT, Copyright (c) 2026 Zeph Leggett), which this project studied while the link was
being designed, and some behaviour is adapted from it: the held-frame and hand-back rules (`remote_state.py`, `joining.py`), the bulk
framing padding rules (`framing.py`), the FunctionFS descriptor layout and hazard list (`ffs.py`, `scripts/ngp-bigmodel-root.sh`),
and the swap-while-not-in-control and small-model-warm-up rules. Where code from Jetlink is copied or closely adapted, keep this notice.
Jetlink's licence follows.

```
MIT License

Copyright (c) 2026 Zeph Leggett

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
