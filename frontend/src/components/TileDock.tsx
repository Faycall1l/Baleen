import type { ReactNode } from 'react';

import { TILE_TITLES, type TileId } from '../tileState';

interface Props {
  tiles: TileId[];
  enlarged: TileId | null;
  onClose: (id: TileId) => void;
  onMove: (id: TileId, dir: -1 | 1) => void;
  onEnlarge: (id: TileId | null) => void;
  onOpen: (id: TileId) => void;
  renderBody: (id: TileId) => ReactNode;
}

/** Bloomberg-style tile dock (P1, unified in P5, freed in P6): rail-opened
 * panels share the main area side-by-side. Slim title bars carry reorder,
 * enlarge, and close; every tile can close, and an empty dock offers
 * quick-open so no one gets stranded.
 */
export default function TileDock({ tiles, enlarged, onClose, onMove, onEnlarge, onOpen, renderBody }: Props) {
  if (tiles.length === 0) {
    return (
      <div className="tile-dock tile-dock-empty">
        <div className="tile-empty" role="status">
          <div>All panels closed.</div>
          <div className="tile-empty-actions">
            {(Object.keys(TILE_TITLES) as TileId[]).map((id) => (
              <button key={id} type="button" className="graph-fit-btn" onClick={() => onOpen(id)}>
                {TILE_TITLES[id]}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }
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
                <button
                  type="button"
                  className="tile-btn"
                  title={`Close ${TILE_TITLES[id]}`}
                  aria-label={`Close ${TILE_TITLES[id]}`}
                  onClick={() => onClose(id)}
                >
                  ×
                </button>
              </span>
            </header>
            <div className="tile-body">{renderBody(id)}</div>
          </section>
        );
      })}
    </div>
  );
}
