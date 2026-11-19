---
description: "Use when building or updating this project's frontend UI, React components, or styles. Apply the visual direction from the design reference while following the ticketing system's actual data and behavior."
applyTo: "frontend/src/**"
---

# Frontend visual direction

Use the root-level `desighn_reference.png` as a visual reference for color palette and overall style only.

- Favor a dark navy/near-black foundation, bright purple-to-cyan accents, restrained neon gradients or glows, and clear white/muted-gray text.
- Use polished card and panel surfaces with subtle gradient borders and rounded corners where they suit the screen.
- Treat the image as inspiration, not a finished page design or a specification to copy. Do not reproduce its layout, placeholder text, sample events, or controls verbatim.
- Design each screen around this application's real models, fields, permissions, and workflows. Use accurate ticket/event data and labels; do not invent product fields or behavior to match the reference.
- Keep the UI responsive, readable, and accessible, with sufficient contrast and visible interaction states.
- Implement and maintain both light and dark themes across the entire site, with an accessible theme toggle in the header. Persist the user's selected theme between visits.
