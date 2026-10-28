"""Frame-to-frame global motion (dx, dy px, scale) via LK features + RANSAC similarity. Usage: motion.py video [t0 t1]"""
import sys, cv2, numpy as np, json
def analyze(path, t0=0, t1=1e9, W=360):
    cap = cv2.VideoCapture(path); fps = cap.get(cv2.CAP_PROP_FPS)
    cap.set(cv2.CAP_PROP_POS_MSEC, t0*1000)
    prev=None; out=[]; t=t0; dup=0
    while True:
        ok, f = cap.read()
        if not ok or t > t1: break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY); k = W/g.shape[1]; g = cv2.resize(g,None,fx=k,fy=k)
        if prev is not None:
            if np.abs(g.astype(int)-prev.astype(int)).mean() < 0.3: dup+=1
            p0 = cv2.goodFeaturesToTrack(prev, 400, 0.01, 6)
            p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, p0, None)
            m, inl = cv2.estimateAffinePartial2D(p0[st==1], p1[st==1], method=cv2.RANSAC, ransacReprojThreshold=1.5)
            if m is None: m = np.array([[1,0,0],[0,1,0]],float)
            s = float(np.hypot(m[0,0], m[1,0])); out.append((round(t,3), float(m[0,2])/k, float(m[1,2])/k, s))
        prev = g; t += 1/fps
    return fps, out, dup
def summary(path, t0=0, t1=1e9):
    fps, o, dup = analyze(path, t0, t1)
    dx = np.array([r[1] for r in o]); dy = np.array([r[2] for r in o]); sc = np.array([r[3] for r in o])
    cum = np.cumprod(sc)
    rev = lambda a: int(np.sum(np.diff(np.sign(a[np.abs(a) > 0.3])) != 0))
    return dict(file=path, fps=fps, n=len(o), dup_frames=dup,
        mean_abs_dx=round(float(np.abs(dx).mean()),2), mean_abs_dy=round(float(np.abs(dy).mean()),2),
        net_dx=round(float(dx.sum()),1), net_dy=round(float(dy.sum()),1),
        dx_reversals=rev(dx), dy_reversals=rev(dy),
        total_scale=round(float(cum[-1]),4), scale_per_s=round(float(cum[-1]**(fps/len(o))),4),
        scale_steps_shrinking=int((sc<0.999).sum()), scale_steps_growing=int((sc>1.001).sum()),
        jitter_px=round(float(np.hypot(dx-np.convolve(dx,np.ones(9)/9,'same'), dy-np.convolve(dy,np.ones(9)/9,'same')).mean()),2))
if __name__ == "__main__":
    a = sys.argv[1:]; r = summary(a[0], *(map(float, a[1:3])) if len(a) > 1 else ())
    print(json.dumps(r))
