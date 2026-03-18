"""
TrapEngine — colour trapping for screen-print separations.

Expands lighter colours under darker colours to prevent white gaps from
press misregistration. Works on single-channel binary masks (255/0).

Algorithm:
  1. Sort palette indices by Lab L descending (lightest first).
  2. Skip white (L >= 98) and empty masks.
  3. Darkest colour gets 0 trap (defines sharp edges).
  4. Linear interpolation: trap_px = round(maxTrap × (1 - pos / (total - 1))).
  5. For each colour: build union mask of all darker colours, dilate into it.
  6. Dilation: iterative 4-connected, double-buffered, with early termination.

Applied AFTER all MechanicalKnobs, BEFORE layer creation.
Production-only — NOT in 512 px proxy previews (trap sizes are resolution-dependent).
"""

from __future__ import annotations


class TrapEngine:

    @staticmethod
    def apply_trapping(
        masks: list,
        lab_palette: list,
        width: int,
        height: int,
        max_trap_pixels: int,
    ) -> dict:
        """Expand lighter colours into darker territory. Modifies masks in place.

        masks:            list of bytearray binary masks (255/0), one per colour.
        lab_palette:      list of {'L', 'a', 'b'} dicts for lightness sorting.
        max_trap_pixels:  maximum trap expansion in pixels (0 = off, 1-10 typical).

        Returns {'trapped_count': int, 'trap_sizes': list}.
        """
        if max_trap_pixels <= 0 or not masks:
            return {'trapped_count': 0, 'trap_sizes': []}

        palette_size = len(lab_palette)

        # Sort by L descending (lightest first)
        sorted_indices = sorted(range(palette_size), key=lambda i: lab_palette[i]['L'], reverse=True)

        # Filter: skip white (L >= 98) and empty masks
        trappable = []
        for idx in sorted_indices:
            if lab_palette[idx]['L'] >= 98:
                continue
            mask = masks[idx]
            if any(v == 255 for v in mask):
                trappable.append(idx)

        if len(trappable) <= 1:
            return {'trapped_count': 0, 'trap_sizes': []}

        # Linear interpolation of trap sizes
        # trappable[0] = lightest non-white → max trap
        # trappable[-1] = darkest → 0 trap
        trap_sizes = []
        total = len(trappable)
        trapped_count = 0

        for pos, idx in enumerate(trappable):
            trap_px = 0 if total == 1 else round(max_trap_pixels * (1 - pos / (total - 1)))
            entry = {'index': idx, 'trap_px': trap_px, 'expanded_pixels': 0}
            trap_sizes.append(entry)

            if trap_px <= 0:
                continue

            darker_mask = TrapEngine._build_darker_mask(masks, trappable, pos, width, height)
            expanded = TrapEngine._dilate_mask_into(masks[idx], darker_mask, trap_px, width, height)

            entry['expanded_pixels'] = expanded
            if expanded > 0:
                trapped_count += 1

        return {'trapped_count': trapped_count, 'trap_sizes': trap_sizes}

    @staticmethod
    def _build_darker_mask(
        masks: list,
        trappable: list,
        current_pos: int,
        width: int,
        height: int,
    ) -> bytearray:
        """Union mask of all colours darker than current_pos in trappable list."""
        pixel_count = width * height
        darker = bytearray(pixel_count)
        for pos in range(current_pos + 1, len(trappable)):
            mask = masks[trappable[pos]]
            for i in range(pixel_count):
                if mask[i] == 255:
                    darker[i] = 255
        return darker

    @staticmethod
    def _dilate_mask_into(
        mask: bytearray,
        darker_mask: bytearray,
        iterations: int,
        width: int,
        height: int,
    ) -> int:
        """Iterative 4-connected dilation constrained to darker_mask territory.

        Double-buffered (snapshot per iteration) to prevent order-dependent artefacts.
        Returns total pixels added.
        """
        pixel_count = width * height
        total_added = 0
        snapshot = bytearray(pixel_count)

        for _ in range(iterations):
            snapshot[:] = mask

            added_this = 0
            for y in range(height):
                for x in range(width):
                    i = y * width + x
                    if mask[i] != 0:
                        continue
                    if darker_mask[i] == 0:
                        continue

                    # Check 4-connected neighbours in snapshot
                    has_neighbor = (
                        (y > 0          and snapshot[i - width] == 255) or
                        (y < height - 1 and snapshot[i + width] == 255) or
                        (x > 0          and snapshot[i - 1]     == 255) or
                        (x < width - 1  and snapshot[i + 1]     == 255)
                    )

                    if has_neighbor:
                        mask[i] = 255
                        added_this += 1

            total_added += added_this
            if added_this == 0:
                break  # No expansion — all reachable territory filled

        return total_added
