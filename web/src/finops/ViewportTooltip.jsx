import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";


const tooltipClosers = new Set();


export function viewportTooltipPosition(anchorRect, tooltipRect, viewport, margin = 12, gap = 10) {
  const width = Math.max(0, Number(tooltipRect?.width) || 0);
  const height = Math.max(0, Number(tooltipRect?.height) || 0);
  const viewportWidth = Math.max(width + (margin * 2), Number(viewport?.width) || 0);
  const viewportHeight = Math.max(height + (margin * 2), Number(viewport?.height) || 0);
  const anchorCenter = (Number(anchorRect?.left) || 0) + ((Number(anchorRect?.width) || 0) / 2);
  const left = Math.round(Math.min(
    viewportWidth - width - margin,
    Math.max(margin, anchorCenter - (width / 2)),
  ));
  const above = (Number(anchorRect?.top) || 0) - height - gap;
  const below = (Number(anchorRect?.bottom) || 0) + gap;
  const top = Math.round(Math.min(
    viewportHeight - height - margin,
    Math.max(margin, above >= margin ? above : below),
  ));
  return { left, top };
}


export function useViewportTooltipAnchor() {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const close = () => setOpen(false);
    tooltipClosers.add(close);
    return () => tooltipClosers.delete(close);
  }, []);
  const show = () => {
    tooltipClosers.forEach((close) => close());
    setOpen(true);
  };
  const hide = () => setOpen(false);
  const toggle = () => (open ? hide() : show());
  const showFromPointer = () => {
    const activeElement = document.activeElement;
    if (
      activeElement instanceof Element
      && activeElement !== anchorRef.current
      && activeElement.hasAttribute("data-finops-tooltip-anchor")
    ) return;
    show();
  };
  return {
    anchorRef,
    open,
    setOpen,
    toggle,
    anchorProps: {
      "data-finops-tooltip-anchor": "true",
      onPointerEnter: showFromPointer,
      onPointerLeave: () => {
        if (anchorRef.current !== document.activeElement) hide();
      },
      onFocus: show,
      onBlur: hide,
    },
  };
}


export function ViewportTooltip({ anchorRef, open, id, variant = "", children }) {
  const tooltipRef = useRef(null);
  const [position, setPosition] = useState(null);
  useLayoutEffect(() => {
    if (!open || typeof window === "undefined") return undefined;
    const update = () => {
      if (!anchorRef.current || !tooltipRef.current) return;
      setPosition(viewportTooltipPosition(
        anchorRef.current.getBoundingClientRect(),
        tooltipRef.current.getBoundingClientRect(),
        { width: window.innerWidth, height: window.innerHeight },
      ));
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [anchorRef, open]);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div
      className={`finops-viewport-tooltip ${variant}`.trim()}
      id={id}
      ref={tooltipRef}
      role="tooltip"
      style={{
        left: `${position?.left ?? -9999}px`,
        top: `${position?.top ?? -9999}px`,
        visibility: position ? "visible" : "hidden",
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
