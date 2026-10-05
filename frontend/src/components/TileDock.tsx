import type { ReactNode } from 'react';

import { TILE_TITLES, type TileId } from '../tileState';

interface Props {
  tiles: TileId[];
  enlarged: TileId | null;
  onClose: (id: TileId) => void;
  onMove: (id: TileId, dir: -1 | 1) => void;
  onEnlarge: (id: TileId | null) => void;
  renderBody: (id: TileId) => ReactNode;
}

/** Bloomberg-style tile dock (P1): rail-opened panels share the main area
 * side-by-side instead of stacking as one overlay. Slim title bars carry
 * reorder, enlarge, and close; the board tile cannot close (the rail has
 * no board tab to bring it back). Narrow screens keep the overlay drawer —
 * tiles hide there except the board.
 */
export default function TileDock({ tiles, enlarged, onClose, onMove, onEnlarge, renderBody }: Props) {
  return (
    <div className={`tile-dock${enlarged ? ' has-enlarged' : ''}`}>
      {tiles.map((id) => {
        const isEnlarged = enlarged === id;
        return (
          <section
            key={id}
            aria-label={TILE_TITLES[id]}
            className={`tile${id === 'board' ? ' tile-board' : ' tile-side'}${isEnlarged ? ' enlarged' : ''}`}
          >
            <header className="tile-bar">
              <span className="tile-title">{TILE_TITLES[id]}</span>
              <span className="tile-actions">
                <button
                  type="button"
                  className="tile-btn"
                  title={`Move ${TILE_TITLES[id]} left`}
                  aria-label={`Move ${TILE_TITLES[id]} left`}
                  onClick={() => onMove(id, -1)}
                >
                  ←
                </button>
                <button
                  type="button"
                  className="tile-btn"
                  title={`Move ${TILE_TITLES[id]} right`}
                  aria-label={`Move ${TILE_TITLES[id]} right`}
                  onClick={() => onMove(id, 1)}
                >
                  →
                </button>
                <button
                  type="button"
                  className="tile-btn"
                  title={isEnlarged ? 'Restore' : `Enlarge ${TILE_TITLES[id]}`}
                  aria-label={isEnlarged ? 'Restore' : `Enlarge ${TILE_TITLES[id]}`}
                  aria-pressed={isEnlarged}
                  onClick={() => onEnlarge(isEnlarged ? null : id)}
                >
                  {isEnlarged ? '⤡' : '⤢'}
                </button>
                {id !== 'board' && (
                  <button
                    type="button"
                    className="tile-btn"
                    title={`Close ${TILE_TITLES[id]}`}
                    aria-label={`Close ${TILE_TITLES[id]}`}
                    onClick={() => onClose(id)}
                  >
                    ×
                  </button>
                )}
              </span>
            </header>
            <div className="tile-body">{renderBody(id)}</div>
          </section>
        );
      })}
    </div>
  );
}
