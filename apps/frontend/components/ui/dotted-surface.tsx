"use client"

import * as React from "react"
import * as THREE from "three"
import { useTheme } from "next-themes"

import { cn } from "@/lib/utils"

type DottedSurfaceProps = Omit<React.ComponentProps<"div">, "ref"> & {
  size?: number
  opacity?: number
  sizeAttenuation?: boolean
  vertexColors?: boolean
}

export function DottedSurface({
  className,
  size = 8,
  opacity = 0.8,
  sizeAttenuation = true,
  vertexColors = true,
  ...props
}: DottedSurfaceProps) {
  const { resolvedTheme } = useTheme()
  const containerRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const scene = new THREE.Scene()
    scene.fog = new THREE.Fog(resolvedTheme === "dark" ? 0x171717 : 0xffffff, 2000, 10000)
    const camera = new THREE.PerspectiveCamera(60, 1, 1, 10000)
    camera.position.set(0, 355, 1220)
    camera.lookAt(0, 0, 0)

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor(scene.fog.color, 0)
    renderer.domElement.style.width = "100%"
    renderer.domElement.style.height = "100%"
    renderer.domElement.style.display = "block"
    container.appendChild(renderer.domElement)

    const separation = 150
    const amountX = 40
    const amountY = 60
    const positions: number[] = []
    const colors: number[] = []
    const dark = resolvedTheme === "dark"

    for (let ix = 0; ix < amountX; ix++) {
      for (let iy = 0; iy < amountY; iy++) {
        positions.push(
          ix * separation - (amountX * separation) / 2,
          0,
          iy * separation - (amountY * separation) / 2,
        )
        colors.push(...(dark ? [0.72, 0.8, 0.95] : [0.1, 0.38, 0.9]))
      }
    }

    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3))
    geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3))
    const material = new THREE.PointsMaterial({
      size,
      vertexColors,
      transparent: true,
      opacity,
      sizeAttenuation,
    })
    if (!vertexColors) material.color.set(dark ? 0xc8c8c8 : 0x2563eb)
    const points = new THREE.Points(geometry, material)
    scene.add(points)

    const resize = () => {
      const { width, height } = container.getBoundingClientRect()
      if (!width || !height) return
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setSize(width, height, false)
    }
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    resize()

    const reduzirMovimento = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    let count = 0
    let animationId = 0
    const animate = () => {
      if (!reduzirMovimento) animationId = requestAnimationFrame(animate)
      const attribute = geometry.attributes.position as THREE.BufferAttribute
      const array = attribute.array as Float32Array
      let i = 0
      for (let ix = 0; ix < amountX; ix++) {
        for (let iy = 0; iy < amountY; iy++) {
          array[i * 3 + 1] = Math.sin((ix + count) * 0.3) * 50 + Math.sin((iy + count) * 0.5) * 50
          i++
        }
      }
      attribute.needsUpdate = true
      renderer.render(scene, camera)
      count += 0.1
    }
    animate()

    return () => {
      cancelAnimationFrame(animationId)
      observer.disconnect()
      geometry.dispose()
      material.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [resolvedTheme, size, opacity, sizeAttenuation, vertexColors])

  return <div ref={containerRef} className={cn("pointer-events-none absolute inset-0", className)} aria-hidden="true" {...props} />
}

export default DottedSurface
