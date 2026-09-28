import { useEffect, useRef, useState } from 'react';

const N = 18;
const SPOKE = 0.13;
const NEIGHBOR = 0.3;
const INTERNAL_DAMP = 0.85;
const AIR = 0.997;
const LIFT_REACH = 3;
const LIFT_WEIGHT = 0.18;
const MAX_THROW = 28;
const MAX_OBJECTS = 5;

// Idle backoff — once an object has settled (avg node speed below REST_EPSILON
// for REST_THRESHOLD_FRAMES) and isn't being dragged, its physics update only
// runs 1 out of every REST_SKIP_FRAMES frames instead of every frame at 60fps.
// It's still redrawn every frame (frozen in place) so nothing visibly changes;
// any drag or renewed motion resumes full rate immediately.
const REST_EPSILON = 0.05;
const REST_THRESHOLD_FRAMES = 60;
const REST_SKIP_FRAMES = 6;

// Physics runs in fixed 60 Hz steps, however fast the screen refreshes. It
// used to step once per frame, so a phone dropping to 30 fps (battery saver,
// a busy page) ran everything at half speed, and a 120 Hz screen at double.
const STEP_MS = 1000 / 60;
const MAX_FRAME_MS = 100;     // after a stall, catch up at most this much
// Extra grab radius outside a shape's outline: a fingertip is ~40px wide and
// the smallest shapes are 36px across, so an exact test missed most taps.
const GRAB_SLOP_TOUCH = 22;
const GRAB_SLOP_MOUSE = 4;

type Shape = 'circle' | 'triangle' | 'square';

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

interface Params {
  size: number;
  bounce: number;
  gravity: number;
  squish: number;
}

interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
  midX: number;
}

interface PhysObject {
  id: string;
  shape: Shape;
  nodes: Node[];
  template: { ox: number; oy: number }[];
  center: { x: number; y: number };
  isDragging: boolean;
  grabbedNodeIndex: number;
  pointerX: number;
  pointerY: number;
  pointerSamples: { x: number; y: number; t: number }[];
  params: Params;
  restArea: number;
  restChord: number;
  selected: boolean;
  idleFrames: number;
  restSkipCounter: number;
  hueShift: number;
  rotation: number;
  baseSpin: number;
  spinBoost: number;
  grounded: boolean;
  cornerStep: number;
}

const DEFAULT_PARAMS: Params = { size: 60, bounce: 0.78, gravity: 0.25, squish: 0.0007 };

// Unit-scale offsets around the shape's outline, evenly spaced by perimeter
// position. Circles sample by angle; triangle/square sample along their
// (equal-length, since regular) edges — the same bezier-spline renderer then
// rounds the corners into a soft, wobbly silhouette for all three.
function shapeTemplate(shape: Shape, n: number): { ox: number; oy: number }[] {
  if (shape === 'circle') {
    return Array.from({ length: n }, (_, i) => {
      const a = (i / n) * Math.PI * 2;
      return { ox: Math.cos(a), oy: Math.sin(a) };
    });
  }
  const verts = shape === 'triangle' ? 3 : 4;
  // Triangle starts point-up (-90deg); square starts at -45deg so its sides
  // land flat (top/bottom/left/right) instead of rendering as a diamond.
  const startAngle = shape === 'triangle' ? -Math.PI / 2 : -Math.PI / 4;
  const corners = Array.from({ length: verts }, (_, i) => {
    const a = startAngle + (i * Math.PI * 2) / verts;
    return { x: Math.cos(a), y: Math.sin(a) };
  });
  return Array.from({ length: n }, (_, i) => {
    const t = (i / n) * verts;
    const seg = Math.floor(t) % verts;
    const frac = t - Math.floor(t);
    const a = corners[seg];
    const b = corners[(seg + 1) % verts];
    return { ox: a.x + (b.x - a.x) * frac, oy: a.y + (b.y - a.y) * frac };
  });
}

function shoelaceArea(pts: { ox: number; oy: number }[]): number {
  let area = 0;
  for (let i = 0; i < pts.length; i++) {
    const p1 = pts[i];
    const p2 = pts[(i + 1) % pts.length];
    area += p1.ox * p2.oy - p2.ox * p1.oy;
  }
  return Math.abs(area / 2);
}

function averageChordLen(pts: { ox: number; oy: number }[]): number {
  let sum = 0;
  for (let i = 0; i < pts.length; i++) {
    const p1 = pts[i];
    const p2 = pts[(i + 1) % pts.length];
    sum += Math.hypot(p2.ox - p1.ox, p2.oy - p1.oy);
  }
  return sum / pts.length;
}

function pointInPolygon(px: number, py: number, nodes: Node[]): boolean {
  let inside = false;
  for (let i = 0, j = nodes.length - 1; i < nodes.length; j = i++) {
    const xi = nodes[i].x, yi = nodes[i].y;
    const xj = nodes[j].x, yj = nodes[j].y;
    const intersect = yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

function randomShape(): Shape {
  const r = Math.random();
  if (r < 0.6) return 'circle';
  if (r < 0.8) return 'triangle';
  return 'square';
}

function hexToHsl(hex: string): { h: number; s: number; l: number } {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return { h: 100, s: 100, l: 55 }; // fallback: roughly the default neon green
  const r = parseInt(m[1].slice(0, 2), 16) / 255;
  const g = parseInt(m[1].slice(2, 4), 16) / 255;
  const b = parseInt(m[1].slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l: l * 100 };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) * 60;
  else if (max === g) h = ((b - r) / d + 2) * 60;
  else h = ((r - g) / d + 4) * 60;
  return { h, s: s * 100, l: l * 100 };
}

function applyParams(obj: PhysObject, params: Params): void {
  obj.params = params;
  obj.restArea = shoelaceArea(obj.template) * params.size * params.size;
  obj.restChord = averageChordLen(obj.template) * params.size;
  obj.idleFrames = 0;   // resettle at full rate
}

function randomParams(): Params {
  const rand = (min: number, max: number) => min + Math.random() * (max - min);
  return {
    size: rand(18, 80),
    bounce: rand(0.3, 0.95),
    gravity: rand(0.05, 0.6),
    squish: rand(0.0003, 0.0015),
  };
}

function createObject(id: string, shape: Shape, center: { x: number; y: number }, initialParams: Params = DEFAULT_PARAMS): PhysObject {
  const template = shapeTemplate(shape, N);
  const params = { ...initialParams };
  const nodes: Node[] = template.map(t => ({
    x: center.x + t.ox * params.size,
    y: center.y + t.oy * params.size,
    vx: 0,
    vy: 0,
  }));
  // Triangles/squares get a slow ambient tumble; circles are radially
  // symmetric so a baseline spin would be invisible — skip it for them.
  const baseSpin = shape === 'circle' ? 0 : (Math.random() < 0.5 ? -1 : 1) * (0.006 + Math.random() * 0.012);
  // Angle between equivalent "flat side down" orientations — a regular
  // triangle/square looks identical every 1/3 or 1/4 turn, so once grounded
  // we ease rotation toward the nearest multiple of this to land flat
  // instead of balancing on a corner. Circles have no such preferred angle.
  const cornerStep = shape === 'triangle' ? (Math.PI * 2) / 3 : shape === 'square' ? (Math.PI * 2) / 4 : 0;
  return {
    id,
    shape,
    nodes,
    template,
    center: { ...center },
    isDragging: false,
    grabbedNodeIndex: -1,
    pointerX: 0,
    pointerY: 0,
    pointerSamples: [],
    params,
    restArea: shoelaceArea(template) * params.size * params.size,
    restChord: averageChordLen(template) * params.size,
    selected: false,
    idleFrames: 0,
    restSkipCounter: 0,
    hueShift: (Math.random() - 0.5) * 80, // +/-40deg, so each object stands out slightly
    rotation: 0,
    baseSpin,
    spinBoost: 0,
    grounded: false,
    cornerStep,
  };
}

function ShapeIcon({ shape }: { shape: Shape }) {
  if (shape === 'triangle') {
    return (
      <div
        style={{
          width: 0,
          height: 0,
          borderLeft: '6px solid transparent',
          borderRight: '6px solid transparent',
          borderBottom: '10px solid var(--accent)',
        }}
      />
    );
  }
  return (
    <div
      className={shape === 'circle' ? 'rounded-full' : ''}
      style={{ width: '11px', height: '11px', background: 'var(--accent)' }}
    />
  );
}

export default function StressBall() {
  const nextIdRef = useRef(0);
  const objectsRef = useRef<PhysObject[]>([]);
  const draggingObjRef = useRef<PhysObject | null>(null);

  const [objectsMeta, setObjectsMeta] = useState<{ id: string; shape: Shape }[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [panelParams, setPanelParams] = useState<Params>({ ...DEFAULT_PARAMS });
  const [isOpen, setIsOpen] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const isTabVisible = isHovered || isOpen;

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationFrameRef = useRef<number | null>(null);
  // Last shape a pointer press grabbed, for the double-click fallback below.
  const lastGrabRef = useRef<{ id: string; t: number } | null>(null);

  const collidersRef = useRef<Rect[]>([]);
  // Set when something visible changed without physics running (selection,
  // a deleted shape, resize, theme): the canvas is only redrawn when needed.
  const dirtyRef = useRef(true);
  const accentHslRef = useRef<{ h: number; s: number; l: number }>({ h: 100, s: 100, l: 55 });
  const stateRef = useRef({
    width: window.innerWidth,
    height: window.innerHeight,
    ceilingY: 0,
  });

  const selectObject = (id: string | null) => {
    for (const o of objectsRef.current) o.selected = o.id === id;
    dirtyRef.current = true;
    setSelectedId(id);
    if (id) {
      const obj = objectsRef.current.find(o => o.id === id);
      if (obj) setPanelParams({ ...obj.params });
      setIsOpen(true);
    }
  };

  const handleAdd = () => {
    if (objectsRef.current.length >= MAX_OBJECTS) return;
    const shape = randomShape();
    const count = objectsRef.current.length;
    const cx = Math.max(100, Math.min(window.innerWidth - 100, window.innerWidth / 2 + (count - 2) * 90));
    const cy = 130 + (count % 2) * 70;
    const id = `obj-${nextIdRef.current++}`;
    const obj = createObject(id, shape, { x: cx, y: cy }, randomParams());
    objectsRef.current = [...objectsRef.current, obj];
    setObjectsMeta(objectsRef.current.map(o => ({ id: o.id, shape: o.shape })));
    selectObject(id);
  };

  const handleDelete = (id: string) => {
    objectsRef.current = objectsRef.current.filter(o => o.id !== id);
    dirtyRef.current = true;
    setObjectsMeta(objectsRef.current.map(o => ({ id: o.id, shape: o.shape })));
    setSelectedId(curr => (curr === id ? null : curr));
  };

  const handleReset = () => {
    if (!selectedId) return;
    const obj = objectsRef.current.find(o => o.id === selectedId);
    if (!obj) return;
    const defaults = { ...DEFAULT_PARAMS };
    applyParams(obj, defaults);
    obj.rotation = 0;
    obj.spinBoost = 0;
    for (let i = 0; i < N; i++) {
      obj.nodes[i].x = obj.center.x + obj.template[i].ox * defaults.size;
      obj.nodes[i].y = obj.center.y + obj.template[i].oy * defaults.size;
      obj.nodes[i].vx = 0;
      obj.nodes[i].vy = 0;
    }
    setPanelParams(defaults);
  };

  const updateParam = (key: keyof Params, value: number) => {
    setPanelParams(prev => {
      const next = { ...prev, [key]: value };
      const obj = objectsRef.current.find(o => o.id === selectedId);
      if (obj) applyParams(obj, next);
      return next;
    });
  };

  // Spawn the initial default ball on first mount, unselected — selection
  // (and its dashed highlight) only happens in response to a deliberate
  // double-click or panel-icon click, never automatically.
  useEffect(() => {
    if (objectsRef.current.length === 0) {
      const id = `obj-${nextIdRef.current++}`;
      const obj = createObject(id, 'circle', { x: window.innerWidth / 2, y: 150 });
      objectsRef.current = [obj];
      setObjectsMeta([{ id, shape: 'circle' }]);
    }
  }, []);

  // Backspace deletes the currently selected object (ignored while a text
  // field elsewhere on the page has focus, so it doesn't hijack normal typing).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Backspace') return;
      const target = e.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (!selectedId) return;
      e.preventDefault();
      handleDelete(selectedId);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedId]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rebuildColliders = () => {
      collidersRef.current = Array.from(document.querySelectorAll('[data-collider]')).map(el => {
        const rect = el.getBoundingClientRect();
        return {
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          midX: rect.left + rect.width / 2,
        };
      });
    };

    const updateAccentColor = () => {
      const hex = getComputedStyle(document.body).getPropertyValue('--accent').trim() || '#39ff14';
      accentHslRef.current = hexToHsl(hex);
      dirtyRef.current = true;
    };
    updateAccentColor();
    const themeObserver = new MutationObserver(updateAccentColor);
    themeObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });

    const resizeCanvas = () => {
      const width = window.innerWidth;
      const height = window.innerHeight;
      if (width === 0 || height === 0) return;

      // Capped at 2: phones report 3, which makes every frame 2.25x the
      // pixels of a 2x canvas for no visible gain on soft, glowing shapes.
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const navElement = document.querySelector('nav');
      let ceilingY = 0;
      if (navElement) {
        ceilingY = navElement.getBoundingClientRect().bottom;
      }

      const s = stateRef.current;
      s.width = width;
      s.height = height;
      s.ceilingY = ceilingY;
      rebuildColliders();
      dirtyRef.current = true;
    };

    // Scroll fires many times per frame on a phone, and each rebuild measures
    // every panel; mark the colliders stale instead and rebuild once per frame.
    let collidersStale = true;
    const markCollidersStale = () => { collidersStale = true; };
    const coarsePointer = window.matchMedia?.('(pointer: coarse)').matches ?? false;

    window.addEventListener('resize', resizeCanvas);
    window.addEventListener('scroll', markCollidersStale, { passive: true });
    const rebuildInterval = setInterval(markCollidersStale, 500);
    resizeCanvas();

    // Top-most shape under the point, or failing that the nearest one whose
    // outline is within `slop` px of it.
    const hitTest = (x: number, y: number, slop: number): PhysObject | null => {
      const objs = objectsRef.current;
      for (let idx = objs.length - 1; idx >= 0; idx--) {
        if (pointInPolygon(x, y, objs[idx].nodes)) return objs[idx];
      }
      let best: PhysObject | null = null;
      let bestGap = slop;
      for (const o of objs) {
        const gap = Math.hypot(x - o.center.x, y - o.center.y) - o.params.size;
        if (gap < bestGap) { best = o; bestGap = gap; }
      }
      return best;
    };
    const slopFor = (pointerType: string) => (pointerType === 'mouse' ? GRAB_SLOP_MOUSE : GRAB_SLOP_TOUCH);

    const startDrag = (e: PointerEvent) => {
      if ((e.target as HTMLElement | null)?.closest?.('[data-physics-ui]')) return;
      const obj = hitTest(e.clientX, e.clientY, slopFor(e.pointerType));
      if (!obj) return;
      e.preventDefault();
      e.stopPropagation();
      obj.isDragging = true;
      obj.pointerX = e.clientX;
      obj.pointerY = e.clientY;

      let minDist = Infinity;
      obj.grabbedNodeIndex = 0;
      for (let i = 0; i < N; i++) {
        const d = Math.hypot(obj.nodes[i].x - obj.pointerX, obj.nodes[i].y - obj.pointerY);
        if (d < minDist) {
          minDist = d;
          obj.grabbedNodeIndex = i;
        }
      }
      obj.pointerSamples = [{ x: obj.pointerX, y: obj.pointerY, t: performance.now() }];
      draggingObjRef.current = obj;
      lastGrabRef.current = { id: obj.id, t: performance.now() };
      obj.idleFrames = 0;
    };

    // On a touchscreen the browser pans the page under a dragged shape and
    // then cancels the drag (pointercancel), which read as a bad hitbox.
    // Pointer events can't stop that; a non-passive touchstart on a shape can.
    const onTouchStart = (e: TouchEvent) => {
      if ((e.target as HTMLElement | null)?.closest?.('[data-physics-ui]')) return;
      const t = e.touches[0];
      if (t && hitTest(t.clientX, t.clientY, GRAB_SLOP_TOUCH)) e.preventDefault();
    };
    const onTouchMove = (e: TouchEvent) => {
      if (draggingObjRef.current) e.preventDefault();
    };

    const moveDrag = (e: PointerEvent) => {
      const obj = draggingObjRef.current;
      if (!obj || !obj.isDragging) return;
      e.preventDefault();
      e.stopPropagation();
      obj.pointerX = e.clientX;
      obj.pointerY = e.clientY;
      const now = performance.now();
      obj.pointerSamples.push({ x: obj.pointerX, y: obj.pointerY, t: now });

      while (obj.pointerSamples.length > 0 && now - obj.pointerSamples[0].t > 100) {
        obj.pointerSamples.shift();
      }
    };

    const endDrag = (e: PointerEvent) => {
      const obj = draggingObjRef.current;
      if (!obj || !obj.isDragging) return;
      e.preventDefault();
      e.stopPropagation();
      obj.isDragging = false;
      draggingObjRef.current = null;

      const now = performance.now();
      while (obj.pointerSamples.length > 0 && now - obj.pointerSamples[0].t > 100) {
        obj.pointerSamples.shift();
      }

      let maxVx = 0, maxVy = 0;
      for (let i = 1; i < obj.pointerSamples.length; i++) {
        const dt = obj.pointerSamples[i].t - obj.pointerSamples[i - 1].t;
        if (dt > 0) {
          const vx = ((obj.pointerSamples[i].x - obj.pointerSamples[i - 1].x) / dt) * 16;
          const vy = ((obj.pointerSamples[i].y - obj.pointerSamples[i - 1].y) / dt) * 16;
          if (Math.abs(vx) > Math.abs(maxVx)) maxVx = vx;
          if (Math.abs(vy) > Math.abs(maxVy)) maxVy = vy;
        }
      }

      const tvx = Math.max(-MAX_THROW, Math.min(MAX_THROW, maxVx));
      const tvy = Math.max(-MAX_THROW, Math.min(MAX_THROW, maxVy));

      if (obj.grabbedNodeIndex !== -1) {
        const grabbed = obj.nodes[obj.grabbedNodeIndex];
        grabbed.vx = tvx;
        grabbed.vy = tvy;

        // Throwing from an off-center point imparts spin — torque from the
        // lever arm (grabbed node vs. center) crossed with the throw velocity.
        const leverX = grabbed.x - obj.center.x;
        const leverY = grabbed.y - obj.center.y;
        const torque = (leverX * tvy - leverY * tvx) * 0.00025;
        obj.spinBoost = Math.max(-0.3, Math.min(0.3, obj.spinBoost + torque));
      }

      let currentArea = 0;
      for (let i = 0; i < N; i++) {
        const p1 = obj.nodes[i];
        const p2 = obj.nodes[(i + 1) % N];
        currentArea += p1.x * p2.y - p2.x * p1.y;
      }
      currentArea = Math.abs(currentArea / 2);

      const compression = Math.max(0, obj.restArea - currentArea) / obj.restArea;
      if (compression > 0.05) {
        const popForce = compression * 15;
        for (let i = 0; i < N; i++) {
          const dx = obj.nodes[i].x - obj.center.x;
          const dy = obj.nodes[i].y - obj.center.y;
          const dist = Math.hypot(dx, dy);
          if (dist > 0.001) {
            obj.nodes[i].vx += (dx / dist) * popForce;
            obj.nodes[i].vy += (dy / dist) * popForce;
          }
        }
      }

      obj.grabbedNodeIndex = -1;
      obj.pointerSamples = [];
    };

    // Double-click selects the object under the cursor (subtle dashed
    // highlight, its own sliders appear in the panel); double-clicking empty
    // space deselects. Clicks inside the tab/panel UI are ignored.
    const handleDblClick = (e: MouseEvent) => {
      if ((e.target as HTMLElement).closest('[data-physics-ui]')) return;
      const objs = objectsRef.current;
      const hit = hitTest(e.clientX, e.clientY, GRAB_SLOP_MOUSE);
      if (hit) {
        selectObject(hit.id);
        return;
      }
      // Nothing under the cursor — but each press of a double-click also grabs
      // and throws the shape, so by the second click it has usually squirmed
      // out from under the pointer. Without this the gesture would land on
      // "empty space" and deselect, making a shape that isn't auto-selected
      // (the one spawned on mount) impossible to edit by double-clicking it.
      const grab = lastGrabRef.current;
      if (grab && performance.now() - grab.t < 600 && objs.some(o => o.id === grab.id)) {
        selectObject(grab.id);
        return;
      }
      selectObject(null);
    };

    window.addEventListener('pointerdown', startDrag, { capture: true });
    window.addEventListener('pointermove', moveDrag, { capture: true });
    window.addEventListener('pointerup', endDrag, { capture: true });
    window.addEventListener('pointercancel', endDrag, { capture: true });
    window.addEventListener('dblclick', handleDblClick, { capture: true });
    window.addEventListener('touchstart', onTouchStart, { capture: true, passive: false });
    window.addEventListener('touchmove', onTouchMove, { capture: true, passive: false });

    let isHidden = document.hidden;
    const handleVisibility = () => { isHidden = document.hidden; };
    document.addEventListener('visibilitychange', handleVisibility);

    // One 60 Hz physics step. Returns whether anything moved (and so needs
    // drawing): resting shapes only step 1 in REST_SKIP_FRAMES.
    const step = (): boolean => {
      let ran = false;
      const s = stateRef.current;
      const colliders = collidersRef.current;
      const objs = objectsRef.current;

      for (const obj of objs) {
        let skipPhysics = false;
        if (!obj.isDragging && obj.idleFrames > REST_THRESHOLD_FRAMES) {
          obj.restSkipCounter = (obj.restSkipCounter + 1) % REST_SKIP_FRAMES;
          skipPhysics = obj.restSkipCounter !== 0;
        }

        if (!skipPhysics) {
          ran = true;
          let cx = 0, cy = 0;
          for (let i = 0; i < N; i++) {
            cx += obj.nodes[i].x;
            cy += obj.nodes[i].y;
          }
          cx /= N; cy /= N;
          obj.center.x = cx; obj.center.y = cy;

          // Ambient tumble only applies in the air — an object resting on
          // the floor/a collider (obj.grounded, set from last frame's
          // contact check below) stops picking up new spin, and any leftover
          // throw-spin bleeds off quickly via friction instead of forever.
          obj.rotation += (obj.grounded ? 0 : obj.baseSpin) + obj.spinBoost;
          obj.spinBoost *= obj.grounded ? 0.75 : 0.95;

          // Landed on a corner instead of a flat side (the rotation isn't
          // near a multiple of cornerStep) — ease it upright onto the
          // nearest flat side, like it's toppling under its own weight.
          if (obj.grounded && !obj.isDragging && obj.cornerStep > 0) {
            const target = Math.round(obj.rotation / obj.cornerStep) * obj.cornerStep;
            const diff = target - obj.rotation;
            obj.rotation += Math.abs(diff) < 0.002 ? diff : diff * 0.12;
          }

          const cosR = Math.cos(obj.rotation);
          const sinR = Math.sin(obj.rotation);

          let currentArea = 0;
          for (let i = 0; i < N; i++) {
            const p1 = obj.nodes[i];
            const p2 = obj.nodes[(i + 1) % N];
            currentArea += p1.x * p2.y - p2.x * p1.y;
          }
          currentArea = Math.abs(currentArea / 2);

          const currentSpoke = obj.isDragging ? SPOKE * 1.7 : SPOKE;
          const currentGravity = obj.isDragging ? obj.params.gravity * 0.3 : obj.params.gravity;

          for (let i = 0; i < N; i++) {
            const node = obj.nodes[i];
            const baseOx = obj.template[i].ox * obj.params.size;
            const baseOy = obj.template[i].oy * obj.params.size;
            const targetX = obj.center.x + baseOx * cosR - baseOy * sinR;
            const targetY = obj.center.y + baseOx * sinR + baseOy * cosR;
            node.vx += (targetX - node.x) * currentSpoke;
            node.vy += (targetY - node.y) * currentSpoke;

            const dx = node.x - obj.center.x;
            const dy = node.y - obj.center.y;
            const dist = Math.hypot(dx, dy);
            if (dist > 0.001) {
              const pressure = (obj.restArea - currentArea) * obj.params.squish;
              node.vx += (dx / dist) * pressure;
              node.vy += (dy / dist) * pressure;
            }

            node.vy += currentGravity;
          }

          for (let i = 0; i < N; i++) {
            const n1 = obj.nodes[i];
            const n2 = obj.nodes[(i + 1) % N];
            const dx = n2.x - n1.x;
            const dy = n2.y - n1.y;
            const dist = Math.hypot(dx, dy);
            if (dist > 0.001) {
              const diff = (dist - obj.restChord) * NEIGHBOR;
              const fx = (dx / dist) * diff;
              const fy = (dy / dist) * diff;
              n1.vx += fx; n1.vy += fy;
              n2.vx -= fx; n2.vy -= fy;
            }
          }

          if (obj.isDragging && obj.grabbedNodeIndex !== -1) {
            obj.nodes[obj.grabbedNodeIndex].x = obj.pointerX;
            obj.nodes[obj.grabbedNodeIndex].y = obj.pointerY;
            obj.nodes[obj.grabbedNodeIndex].vx = 0;
            obj.nodes[obj.grabbedNodeIndex].vy = 0;

            for (let i = 1; i <= LIFT_REACH; i++) {
              const weight = LIFT_WEIGHT * (1 - i / (LIFT_REACH + 1));

              const idx1 = (obj.grabbedNodeIndex + i) % N;
              obj.nodes[idx1].vx += (obj.pointerX - obj.nodes[idx1].x) * weight;
              obj.nodes[idx1].vy += (obj.pointerY - obj.nodes[idx1].y) * weight;

              const idx2 = (obj.grabbedNodeIndex - i + N) % N;
              obj.nodes[idx2].vx += (obj.pointerX - obj.nodes[idx2].x) * weight;
              obj.nodes[idx2].vy += (obj.pointerY - obj.nodes[idx2].y) * weight;
            }
          }

          let avgVx = 0, avgVy = 0;
          for (let i = 0; i < N; i++) {
            avgVx += obj.nodes[i].vx;
            avgVy += obj.nodes[i].vy;
          }
          avgVx /= N; avgVy /= N;

          for (let i = 0; i < N; i++) {
            const node = obj.nodes[i];
            if (obj.isDragging && i === obj.grabbedNodeIndex) continue;
            const devX = node.vx - avgVx;
            const devY = node.vy - avgVy;
            node.vx = avgVx * AIR + devX * INTERNAL_DAMP;
            node.vy = avgVy * AIR + devY * INTERNAL_DAMP;
          }

          let speedSum = 0;
          for (let i = 0; i < N; i++) {
            speedSum += Math.hypot(obj.nodes[i].vx, obj.nodes[i].vy);
          }
          obj.idleFrames = (speedSum / N) < REST_EPSILON ? obj.idleFrames + 1 : 0;

          let touchingTopRect: Rect | null = null;
          let touchedGround = false;

          for (let i = 0; i < N; i++) {
            const node = obj.nodes[i];
            if (obj.isDragging && i === obj.grabbedNodeIndex) continue;

            node.x += node.vx;
            node.y += node.vy;

            for (const rect of colliders) {
              if (node.x > rect.left && node.x < rect.right && node.y > rect.top && node.y < rect.bottom) {
                const distLeft = node.x - rect.left;
                const distRight = rect.right - node.x;
                const distTop = node.y - rect.top;
                const distBottom = rect.bottom - node.y;

                const minDist = Math.min(distLeft, distRight, distTop, distBottom);

                if (minDist === distLeft) {
                  node.x = rect.left;
                  if (node.vx > 0) node.vx *= -obj.params.bounce;
                } else if (minDist === distRight) {
                  node.x = rect.right;
                  if (node.vx < 0) node.vx *= -obj.params.bounce;
                } else if (minDist === distTop) {
                  node.y = rect.top;
                  if (node.vy > 0) node.vy *= -obj.params.bounce;
                  node.vx *= 0.94;
                  touchingTopRect = rect;
                  touchedGround = true;
                } else if (minDist === distBottom) {
                  node.y = rect.bottom;
                  if (node.vy < 0) node.vy *= -obj.params.bounce;
                }
              }
            }

            if (node.y > s.height) {
              node.y = s.height;
              if (node.vy > 0) node.vy *= -obj.params.bounce;
              node.vx *= 0.94;
              touchedGround = true;
            } else if (node.y < s.ceilingY) {
              node.y = s.ceilingY;
              if (node.vy < 0) node.vy *= -obj.params.bounce;
            }

            if (node.x > s.width) {
              node.x = s.width;
              if (node.vx > 0) node.vx *= -obj.params.bounce;
            } else if (node.x < 0) {
              node.x = 0;
              if (node.vx < 0) node.vx *= -obj.params.bounce;
            }
          }

          obj.grounded = touchedGround;

          if (!obj.isDragging && touchingTopRect) {
            const dir = obj.center.x > touchingTopRect.midX ? 1 : -1;
            const slideForce = 0.08;
            for (let i = 0; i < N; i++) {
              obj.nodes[i].vx += slideForce * dir;
            }
          }
        }
      }

      // Object-to-object collisions: each object is approximated as a circle
      // of radius = its own size (the template's corners sit exactly on that
      // radius, so this is exact for the corners and a close fit elsewhere).
      // Overlap is resolved with a positional separation nudge plus an
      // outward velocity impulse, so objects bump off each other instead of
      // passing through.
      for (let a = 0; a < objs.length; a++) {
        for (let b = a + 1; b < objs.length; b++) {
          const oa = objs[a], ob = objs[b];
          const dx = ob.center.x - oa.center.x;
          const dy = ob.center.y - oa.center.y;
          const dist = Math.hypot(dx, dy);
          const minDist = oa.params.size + ob.params.size;
          if (dist > 0.001 && dist < minDist) {
            const nx = dx / dist, ny = dy / dist;
            const overlap = minDist - dist;
            const separation = overlap * 0.5;
            const impulse = Math.min(overlap * 0.05, 2.5);

            for (let i = 0; i < N; i++) {
              oa.nodes[i].x -= nx * separation;
              oa.nodes[i].y -= ny * separation;
              oa.nodes[i].vx -= nx * impulse;
              oa.nodes[i].vy -= ny * impulse;

              ob.nodes[i].x += nx * separation;
              ob.nodes[i].y += ny * separation;
              ob.nodes[i].vx += nx * impulse;
              ob.nodes[i].vy += ny * impulse;
            }
            oa.center.x -= nx * separation;
            oa.center.y -= ny * separation;
            ob.center.x += nx * separation;
            ob.center.y += ny * separation;
            oa.idleFrames = 0;
            ob.idleFrames = 0;
            ran = true;
          }
        }
      }
      return ran;
    };

    const draw = () => {
      const s = stateRef.current;
      const objs = objectsRef.current;
      const { h: accentH, s: accentS, l: accentL } = accentHslRef.current;
      ctx.clearRect(0, 0, s.width, s.height);

      for (const obj of objs) {
        const h = (accentH + obj.hueShift + 360) % 360;
        const fillColor = `hsl(${h.toFixed(1)} ${accentS.toFixed(0)}% ${accentL.toFixed(0)}%)`;

        ctx.beginPath();
        for (let i = 0; i < N; i++) {
          const p0 = obj.nodes[(i - 1 + N) % N];
          const p1 = obj.nodes[i];
          const p2 = obj.nodes[(i + 1) % N];
          const p3 = obj.nodes[(i + 2) % N];

          const cp1x = p1.x + (p2.x - p0.x) / 6;
          const cp1y = p1.y + (p2.y - p0.y) / 6;
          const cp2x = p2.x - (p3.x - p1.x) / 6;
          const cp2y = p2.y - (p3.y - p1.y) / 6;

          if (i === 0) {
            ctx.moveTo(p1.x, p1.y);
          }
          ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
        }
        ctx.closePath();

        ctx.fillStyle = fillColor;
        if (coarsePointer) {
          // shadowBlur is one of the slowest things a phone canvas does; a
          // wide translucent stroke under the fill reads as the same glow.
          ctx.save();
          ctx.globalAlpha = 0.35;
          ctx.lineWidth = 14;
          ctx.strokeStyle = fillColor;
          ctx.stroke();
          ctx.restore();
        } else {
          ctx.shadowColor = fillColor;
          ctx.shadowBlur = 15;
        }
        ctx.fill();

        ctx.lineWidth = 2.5;
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.6)';
        ctx.shadowBlur = 0;
        ctx.stroke();

        if (obj.selected) {
          // Subtle highlight: a darker shade of the object's own hue rather
          // than a stark white outline, so it reads as "this one" without
          // fighting the neon palette.
          const darkOutline = `hsl(${h.toFixed(1)} ${accentS.toFixed(0)}% ${(accentL * 0.55).toFixed(0)}%)`;
          ctx.save();
          ctx.setLineDash([5, 4]);
          ctx.lineWidth = 3;
          ctx.strokeStyle = darkOutline;
          ctx.shadowBlur = 0;
          ctx.stroke();
          ctx.restore();
        }
      }
    };

    let last = performance.now();
    let acc = 0;
    const tick = (now: number) => {
      animationFrameRef.current = requestAnimationFrame(tick);
      if (isHidden) { last = now; return; }
      acc += Math.min(now - last, MAX_FRAME_MS);
      last = now;
      if (collidersStale) { rebuildColliders(); collidersStale = false; }
      let moved = false;
      while (acc >= STEP_MS) {
        if (step()) moved = true;
        acc -= STEP_MS;
      }
      if (moved || dirtyRef.current) {
        draw();
        dirtyRef.current = false;
      }
    };

    animationFrameRef.current = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      window.removeEventListener('scroll', markCollidersStale);
      clearInterval(rebuildInterval);
      themeObserver.disconnect();
      window.removeEventListener('pointerdown', startDrag, { capture: true });
      window.removeEventListener('pointermove', moveDrag, { capture: true });
      window.removeEventListener('pointerup', endDrag, { capture: true });
      window.removeEventListener('pointercancel', endDrag, { capture: true });
      window.removeEventListener('dblclick', handleDblClick, { capture: true });
      window.removeEventListener('touchstart', onTouchStart, { capture: true });
      window.removeEventListener('touchmove', onTouchMove, { capture: true });
      document.removeEventListener('visibilitychange', handleVisibility);
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    };
  }, []);

  return (
    <div className="fixed inset-0 w-screen h-screen z-50 pointer-events-none">
      <canvas
        ref={canvasRef}
        className="block w-full h-full pointer-events-none"
      />

      {/* Pull-tab + control panel — the tab sits flush against the left edge and is
          reduced to a sliver until hovered (or open), like a ribbon bookmark. */}
      <div
        data-physics-ui
        className="absolute bottom-6 left-0 pointer-events-auto flex items-end z-50"
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        {/* Ribbon tab */}
        <div
          onClick={() => setIsOpen(o => !o)}
          className="shrink-0 cursor-pointer select-none flex items-center gap-1 border-2 border-l-0 bg-[var(--bg-card)] font-mono font-bold uppercase transition-transform duration-200 ease-out"
          style={{
            borderColor: 'var(--border)',
            color: 'var(--accent)',
            boxShadow: '-3px 3px 0 0 var(--accent)',
            width: '84px',
            height: '24px',
            paddingLeft: '10px',
            fontSize: '9px',
            letterSpacing: '0.1em',
            transform: isTabVisible ? 'translateX(0)' : 'translateX(-72px)',
          }}
        >
          <span>{isOpen ? '▾' : '▸'}</span>
          <span>PHYSICS</span>
        </div>

        {/* Settings panel */}
        {isOpen && (
          <div
            className="shrink-0 flex flex-col bg-[var(--bg-card)] border-2 border-l-0 text-[9px] font-mono font-bold uppercase tracking-wide w-40"
            style={{ borderColor: 'var(--border)', color: 'var(--text)', boxShadow: '-3px 3px 0 0 var(--accent)' }}
          >
            <div className="p-2.5 flex flex-col gap-2.5">
              <div className="flex items-center justify-between">
                <span className="opacity-70">OBJECTS {objectsMeta.length}/{MAX_OBJECTS}</span>
                <button
                  onClick={handleAdd}
                  disabled={objectsMeta.length >= MAX_OBJECTS}
                  className="border-2 px-1.5 py-0.5 hover:bg-[var(--bg)] transition-colors active:translate-y-px active:shadow-none disabled:opacity-30 disabled:cursor-not-allowed disabled:active:translate-y-0"
                  style={{ borderColor: 'var(--border)', color: 'var(--accent)', boxShadow: '-2px 2px 0 0 var(--accent)' }}
                >
                  [+ ADD]
                </button>
              </div>

              <div className="flex flex-wrap gap-1.5">
                {objectsMeta.map(meta => (
                  <button
                    key={meta.id}
                    onClick={() => selectObject(meta.id === selectedId ? null : meta.id)}
                    className="flex items-center justify-center border-2 w-6 h-6"
                    style={{
                      borderColor: meta.id === selectedId ? 'var(--accent)' : 'var(--border)',
                      boxShadow: meta.id === selectedId ? 'inset 0 0 0 1px var(--accent)' : 'none',
                    }}
                    title={meta.shape}
                  >
                    <ShapeIcon shape={meta.shape} />
                  </button>
                ))}
              </div>

              {selectedId ? (
                <>
                  <div className="flex flex-col gap-1">
                    <div className="flex justify-between">
                      <span>SIZE</span>
                      <span className="opacity-70">{panelParams.size.toFixed(0)}</span>
                    </div>
                    <input type="range" min="18" max="80" step="1" value={panelParams.size} onChange={e => updateParam('size', parseFloat(e.target.value))} className="w-full h-1 bg-[var(--border)] appearance-none outline-none accent-[var(--accent)]" />
                  </div>

                  <div className="flex flex-col gap-1">
                    <div className="flex justify-between">
                      <span>BOUNCE</span>
                      <span className="opacity-70">{panelParams.bounce.toFixed(2)}</span>
                    </div>
                    <input type="range" min="0.3" max="0.95" step="0.01" value={panelParams.bounce} onChange={e => updateParam('bounce', parseFloat(e.target.value))} className="w-full h-1 bg-[var(--border)] appearance-none outline-none accent-[var(--accent)]" />
                  </div>

                  <div className="flex flex-col gap-1">
                    <div className="flex justify-between">
                      <span>GRAVITY</span>
                      <span className="opacity-70">{panelParams.gravity.toFixed(2)}</span>
                    </div>
                    <input type="range" min="0.05" max="0.6" step="0.01" value={panelParams.gravity} onChange={e => updateParam('gravity', parseFloat(e.target.value))} className="w-full h-1 bg-[var(--border)] appearance-none outline-none accent-[var(--accent)]" />
                  </div>

                  <div className="flex flex-col gap-1">
                    <div className="flex justify-between">
                      <span>SQUISH</span>
                      <span className="opacity-70">{panelParams.squish.toFixed(4)}</span>
                    </div>
                    <input type="range" min="0.0003" max="0.0015" step="0.0001" value={panelParams.squish} onChange={e => updateParam('squish', parseFloat(e.target.value))} className="w-full h-1 bg-[var(--border)] appearance-none outline-none accent-[var(--accent)]" />
                  </div>

                  <div className="flex gap-1.5">
                    <button
                      onClick={handleReset}
                      className="flex-1 border-2 p-1.5 hover:bg-[var(--bg)] transition-colors active:translate-y-px active:shadow-none"
                      style={{ borderColor: 'var(--border)', color: 'var(--accent)', boxShadow: '-2px 2px 0 0 var(--accent)' }}
                    >
                      [ RESET ]
                    </button>
                    <button
                      onClick={() => handleDelete(selectedId)}
                      className="flex-1 border-2 p-1.5 hover:bg-[var(--bg)] transition-colors active:translate-y-px active:shadow-none"
                      style={{ borderColor: 'var(--border)', color: '#ff4d4d', boxShadow: '-2px 2px 0 0 #ff4d4d' }}
                    >
                      [ DELETE ]
                    </button>
                  </div>
                </>
              ) : (
                <div className="opacity-60 normal-case tracking-normal py-1 leading-snug">
                  Double-click a shape (or tap its icon above) to edit its settings.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
