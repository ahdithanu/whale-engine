"use client";

// Schematic, authored-from-scratch 3D scene for the Part Fit Qualifier
// reference panel (BRIEF tab). Every mesh here is a primitive built in code
// -- no imported model, no third-party asset. That's deliberate: the source
// reference (a public concept-study site) ships a GM-branded GLB we have no
// rights to redistribute in a tool unrelated to GM. The three real numbers
// that DO come from that reference (36x25ft booth, 25x14ft cart, 20ft rail
// travel, 3,110mm robot reach) drive this scene's proportions and its
// dimension labels; everything else (wall height, arm segment lengths) is
// unlabeled schematic geometry, not asserted as a sourced figure.
//
// The robot arm is real forward kinematics, not a static pose: base swing,
// shoulder, and elbow are nested Three.js groups, each rotated by its own
// slider value, so the joint angles you set in the control panel are what
// actually pose the arm.

import { useEffect, useMemo, useRef } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, Html, ContactShadows } from "@react-three/drei";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";

// FANUC's signature robot-yellow (not the app's UI amber, #F5A623 -- the
// robot is a specific, named, real piece of equipment, so it gets the real
// brand color rather than borrowing the dashboard's accent for convenience).
const FANUC_YELLOW = "#FFC72C";
const JOINT_DARK = "#22262A";

export type CellConfig = {
  mount: "top" | "side" | "wall";
  robots: 1 | 2;
  carriagePct: number; // -50..100, position along rail travel
  baseSwing: number; // deg
  shoulder: number; // deg
  elbow: number; // deg
  layers: {
    walls: boolean;
    roof: boolean;
    workpiece: boolean;
    reach: boolean;
    dimensions: boolean;
    floor: boolean;
  };
  cameraView: "iso" | "top" | "side";
};

// Real, sourced dimensions (feet). See CellReferencePanel for citation.
const BOOTH_W = 36;
const BOOTH_D = 25;
const BOOTH_H = 14; // unlabeled -- not on the reference site, chosen for proportion only
const CART_LEN = 25;
const CART_WID = 14;
const RAIL_TRAVEL = 20;
const REACH_FT = 3110 / 304.8; // 3,110mm robot reach, converted

const MOUNT_HEIGHT: Record<CellConfig["mount"], number> = {
  top: BOOTH_H - 1.5,
  side: BOOTH_H * 0.55,
  wall: 2.5,
};

const CAMERA_VIEWS: Record<CellConfig["cameraView"], { pos: [number, number, number]; target: [number, number, number] }> = {
  iso: { pos: [42, 32, 42], target: [0, 4, 0] },
  top: { pos: [0.01, 62, 0.01], target: [0, 0, 0] },
  side: { pos: [62, 8, 0.01], target: [0, 4, 0] },
};

function CameraRig({ view, controlsRef }: { view: CellConfig["cameraView"]; controlsRef: React.RefObject<OrbitControlsImpl | null> }) {
  const { camera } = useThree();
  useEffect(() => {
    const { pos, target } = CAMERA_VIEWS[view];
    camera.position.set(...pos);
    controlsRef.current?.target.set(...target);
    controlsRef.current?.update();
  }, [view, camera, controlsRef]);
  return null;
}

function RobotArm({ baseSwing, shoulder, elbow, mirrored }: { baseSwing: number; shoulder: number; elbow: number; mirrored?: boolean }) {
  const sign = mirrored ? -1 : 1;
  const deg = (d: number) => (d * Math.PI) / 180;
  return (
    <group rotation-y={deg(baseSwing) * sign} position={[mirrored ? CART_LEN / 2 - 3 : -(CART_LEN / 2 - 3), 1, 0]}>
      {/* base pedestal */}
      <mesh position={[0, 0.6, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[1.1, 1.35, 1.2, 24]} />
        <meshPhysicalMaterial color={JOINT_DARK} metalness={0.6} roughness={0.35} clearcoat={0.2} />
      </mesh>
      {/* shoulder joint + upper arm */}
      <group position={[0, 1.2, 0]} rotation-x={deg(shoulder)}>
        <mesh castShadow>
          <sphereGeometry args={[0.72, 24, 24]} />
          <meshPhysicalMaterial color={JOINT_DARK} metalness={0.6} roughness={0.3} clearcoat={0.3} />
        </mesh>
        <mesh position={[0, 2.2, 0]} castShadow receiveShadow>
          <boxGeometry args={[0.85, 4.4, 0.85]} />
          <meshPhysicalMaterial color={FANUC_YELLOW} metalness={0.15} roughness={0.35} clearcoat={0.5} clearcoatRoughness={0.25} />
        </mesh>
        {/* elbow joint + forearm */}
        <group position={[0, 4.4, 0]} rotation-x={deg(elbow)}>
          <mesh castShadow>
            <sphereGeometry args={[0.58, 24, 24]} />
            <meshPhysicalMaterial color={JOINT_DARK} metalness={0.6} roughness={0.3} clearcoat={0.3} />
          </mesh>
          <mesh position={[0, 2, 0]} castShadow receiveShadow>
            <boxGeometry args={[0.65, 4, 0.65]} />
            <meshPhysicalMaterial color={FANUC_YELLOW} metalness={0.15} roughness={0.35} clearcoat={0.5} clearcoatRoughness={0.25} />
          </mesh>
          {/* end effector / RTU envelope, 1,012mm wide -> ~3.3ft */}
          <mesh position={[0, 4.1, 0]} castShadow receiveShadow>
            <boxGeometry args={[3.3, 1.4, 1.4]} />
            <meshPhysicalMaterial color="#4A5259" metalness={0.5} roughness={0.45} />
          </mesh>
        </group>
      </group>
    </group>
  );
}

// Html (a positioned DOM overlay, not a WebGL text mesh) -- drei's <Text>
// rasterizes an SDF font atlas via troika-three-text, which reliably
// triggers a WebGL context loss on the software (non-GPU) renderer this
// runs under in a sandboxed browser. Html sidesteps that pipeline entirely:
// it's a real <div>, positioned in screen space from the 3D point.
function DimensionLabel({ position, text }: { position: [number, number, number]; text: string }) {
  return (
    <Html position={position} center distanceFactor={30} style={{ pointerEvents: "none" }}>
      <div style={{ fontFamily: "var(--font-plex-mono), monospace", fontSize: 11, color: "#F5A623", whiteSpace: "nowrap" }}>{text}</div>
    </Html>
  );
}

// R3F sizes its canvas via react-use-measure's ResizeObserver on the parent
// container, and gates its first render on that observer's initial
// callback firing. That callback never ran in this app's embedding context
// (confirmed while building this: onCreated never fired, zero draw calls,
// even waiting several seconds). react-use-measure accepts a `polyfill`
// option -- any class shaped like ResizeObserver -- so instead of the
// native browser ResizeObserver (whatever is wrong with it here), this
// supplies a rAF-polling implementation that can't have the same failure
// mode: it reads getBoundingClientRect() every animation frame and only
// invokes the callback when the size actually changes.
type ROCallback = (entries: { target: Element; contentRect: DOMRectReadOnly }[], observer: PollingResizeObserver) => void;

class PollingResizeObserver {
  private cb: ROCallback;
  private el: Element | null = null;
  private raf = 0;
  private last = "";
  constructor(cb: ROCallback) {
    this.cb = cb;
  }
  private tick = () => {
    if (this.el) {
      const rect = this.el.getBoundingClientRect();
      const key = `${rect.width}x${rect.height}`;
      if (key !== this.last) {
        this.last = key;
        this.cb([{ target: this.el, contentRect: rect }], this);
      }
    }
    this.raf = requestAnimationFrame(this.tick);
  };
  observe(target: Element) {
    this.el = target;
    this.raf = requestAnimationFrame(this.tick);
  }
  unobserve() {
    this.el = null;
  }
  disconnect() {
    cancelAnimationFrame(this.raf);
    this.el = null;
  }
}

export default function CellConfigurator3D({ config }: { config: CellConfig }) {
  const controlsRef = useRef<OrbitControlsImpl>(null);
  const { mount, robots, carriagePct, baseSwing, shoulder, elbow, layers, cameraView } = config;

  const carriageX = useMemo(() => RAIL_TRAVEL * ((carriagePct + 50) / 150) - RAIL_TRAVEL / 2, [carriagePct]);
  const mountY = MOUNT_HEIGHT[mount];
  const mountZOffset = mount === "side" ? -(BOOTH_D / 2 - 1.5) : 0;

  return (
    <Canvas dpr={[1, 1.5]} shadows resize={{ polyfill: PollingResizeObserver }} camera={{ fov: 40 }} style={{ background: "#0E1113" }}>
      <hemisphereLight args={["#4A5560", "#0A0C0D", 0.6]} />
      <ambientLight intensity={0.25} />
      <directionalLight position={[24, 34, 14]} intensity={1.4} castShadow shadow-mapSize={[1024, 1024]} shadow-camera-left={-30} shadow-camera-right={30} shadow-camera-top={30} shadow-camera-bottom={-30} />
      <directionalLight position={[-20, 14, -18]} intensity={0.35} color="#8FA6B8" />
      <CameraRig view={cameraView} controlsRef={controlsRef} />
      <OrbitControls ref={controlsRef} makeDefault enableDamping dampingFactor={0.1} minDistance={10} maxDistance={120} />

      {layers.floor && (
        <>
          <gridHelper args={[60, 30, "#2A3238", "#1B2126"]} />
          <mesh rotation-x={-Math.PI / 2} position={[0, -0.01, 0]} receiveShadow>
            <planeGeometry args={[BOOTH_W + 14, BOOTH_D + 14]} />
            <meshStandardMaterial color="#12161A" roughness={0.85} metalness={0.05} />
          </mesh>
          <ContactShadows position={[0, 0, 0]} opacity={0.5} scale={60} blur={2.2} far={20} resolution={512} color="#000000" />
        </>
      )}

      {layers.walls && (
        <group>
          <mesh position={[0, BOOTH_H / 2, -BOOTH_D / 2]}>
            <planeGeometry args={[BOOTH_W, BOOTH_H]} />
            <meshStandardMaterial color="#232A2F" transparent opacity={0.55} side={THREE.DoubleSide} />
          </mesh>
          <mesh position={[-BOOTH_W / 2, BOOTH_H / 2, 0]} rotation-y={Math.PI / 2}>
            <planeGeometry args={[BOOTH_D, BOOTH_H]} />
            <meshStandardMaterial color="#232A2F" transparent opacity={0.4} side={THREE.DoubleSide} />
          </mesh>
          <mesh position={[BOOTH_W / 2, BOOTH_H / 2, 0]} rotation-y={-Math.PI / 2}>
            <planeGeometry args={[BOOTH_D, BOOTH_H]} />
            <meshStandardMaterial color="#232A2F" transparent opacity={0.4} side={THREE.DoubleSide} />
          </mesh>
        </group>
      )}

      {layers.roof && (
        <mesh position={[0, BOOTH_H, 0]} rotation-x={Math.PI / 2}>
          <planeGeometry args={[BOOTH_W, BOOTH_D]} />
          <meshStandardMaterial color="#171B1F" transparent opacity={0.3} side={THREE.DoubleSide} />
        </mesh>
      )}

      {/* rail */}
      <mesh position={[0, mountY, mountZOffset]} rotation-z={Math.PI / 2} castShadow>
        <cylinderGeometry args={[0.35, 0.35, RAIL_TRAVEL + 4, 20]} />
        <meshPhysicalMaterial color="#6B7A84" metalness={0.75} roughness={0.3} />
      </mesh>

      {/* cart */}
      <group position={[carriageX, mountY, mountZOffset]}>
        <mesh castShadow receiveShadow>
          <boxGeometry args={[CART_LEN, 1, CART_WID]} />
          <meshPhysicalMaterial color="#181C20" metalness={0.4} roughness={0.5} clearcoat={0.2} />
        </mesh>
        <mesh>
          <boxGeometry args={[CART_LEN + 0.05, 1.02, CART_WID + 0.05]} />
          <meshBasicMaterial color={FANUC_YELLOW} wireframe />
        </mesh>

        <RobotArm baseSwing={baseSwing} shoulder={shoulder} elbow={elbow} />
        {robots === 2 && <RobotArm baseSwing={baseSwing} shoulder={shoulder} elbow={elbow} mirrored />}

        {layers.reach && (
          <mesh position={[-(CART_LEN / 2 - 3), 3, 0]}>
            <sphereGeometry args={[REACH_FT, 24, 16]} />
            <meshBasicMaterial color={FANUC_YELLOW} wireframe transparent opacity={0.15} />
          </mesh>
        )}
      </group>

      {layers.workpiece && (
        <mesh position={[0, 3, 0]} castShadow receiveShadow>
          <cylinderGeometry args={[1.2, 1.2, 5, 24]} />
          <meshPhysicalMaterial color="#7A8890" metalness={0.6} roughness={0.35} clearcoat={0.15} />
        </mesh>
      )}

      {layers.dimensions && (
        <group>
          <DimensionLabel position={[0, 0.3, BOOTH_D / 2 + 1.5]} text={`${BOOTH_W} × ${BOOTH_D} FT BOOTH`} />
          <DimensionLabel position={[carriageX, mountY + 2, mountZOffset + CART_WID / 2 + 1.5]} text={`${CART_LEN} × ${CART_WID} FT CART`} />
          <DimensionLabel position={[0, mountY - 1.6, mountZOffset]} text={`${RAIL_TRAVEL} FT TRAVEL`} />
        </group>
      )}
    </Canvas>
  );
}
