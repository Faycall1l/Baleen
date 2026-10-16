import { useState, type ReactNode } from 'react';

import GraphErrorBoundary from './GraphErrorBoundary';
import {
  ALL_TILE_IDS,
  TILE_TITLES,
  flattenLayout,
  type Layout,
  type LayoutNode,
  type SplitDir,
  type TileId,
} from '../tileState';

interface Props {
  layout: Layout;
  enlarged: TileId | null;
  onClose: (id: TileId) => void;
  onMove: (id: TileId, dir: -1 | 1) => void;
  onSplit: (id: TileId, dir: SplitDir, panel: TileId) => void;
  onEnlarge: (id: TileId | null) => void;
  renderBody: (id: TileId) => ReactNode;
}

interface FrameProps {
  id: TileId;
  enlarged: boolean;
  closed: TileId[];
  onClose: (id: TileId) => void;
  onMove: (id: TileId, dir: -1 | 1) => void;
  onSplit: (id: TileId, dir: SplitDir, panel: TileId) => void;
  onEnlarge: (id: TileId | null) => void;
  renderBody: (id: TileId) => ReactNode;
}

/** One tile frame: slim title bar with reorder, split, enlarge, close.
 * The split buttons open a picker of currently-closed panels — opening an
 * already-open panel is impossible by construction, so the dock never holds
 * duplicate live instances of one feed.
 */
function TileFrame({ id, enlarged, closed, onClose, onMove, onSplit, onEnlarge, renderBody }: FrameProps) {
  const [picker, setPicker] = useState<SplitDir | null>(null);
  return (
    <section
      aria-label={TILE_TITLES[id]}
      className={`tile${id === 'board' ? ' tile-board' : ' tile-side'}${enlarged ? ' enlarged' : ''}`}
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
          {closed.length > 0 && (
            <>
              <button
                type="button"
                className="tile-btn"
                title={`Split ${TILE_TITLES[id]} vertically (side by side)`}
                aria-label={`Split ${TILE_TITLES[id]} vertically`}
                aria-expanded={picker === 'row'}
                onClick={() => setPicker(picker === 'row' ? null : 'row')}
              >
                ‖
              </button>
              <button
                type="button"
                className="tile-btn"
                title={`Split ${TILE_TITLES[id]} horizontally (stacked)`}
                aria-label={`Split ${TILE_TITLES[id]} horizontally`}
                aria-expanded={picker === 'col'}
                onClick={() => setPicker(picker === 'col' ? null : 'col')}
              >
                ＝
              </button>
            </>
          )}
          <button
            type="button"
            className="tile-btn"
            title={enlarged ? 'Restore' : `Enlarge ${TILE_TITLES[id]}`}
            aria-label={enlarged ? 'Restore' : `Enlarge ${TILE_TITLES[id]}`}
            aria-pressed={enlarged}
            onClick={() => onEnlarge(enlarged ? null : id)}
          >
            {enlarged ? '⤡' : '⤢'}
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
      {picker && (
        <div className="tile-picker" role="menu" aria-label={`Split into ${picker === 'row' ? 'columns' : 'rows'}`}>
          {closed.map((panel) => (
            <button
              key={panel}
              type="button"
              role="menuitem"
              className="tile-pick"
              onClick={() => {
                onSplit(id, picker, panel);
                setPicker(null);
              }}
            >
              {TILE_TITLES[panel]}
            </button>
          ))}
        </div>
      )}
      <div className="tile-body">
        <GraphErrorBoundary key={id}>{renderBody(id)}</GraphErrorBoundary>
      </div>
    </section>
  );
}

function SplitView({
  node,
  enlarged,
  closed,
  onClose,
  onMove,
  onSplit,
  onEnlarge,
  renderBody,
}: {
  node: LayoutNode;
  enlarged: TileId | null;
} & Omit<FrameProps, 'id' | 'enlarged'>) {
  if (node.type === 'tile') {
    return (
      <TileFrame
        id={node.id}
        enlarged={enlarged === node.id}
        closed={closed}
        onClose={onClose}
        onMove={onMove}
        onSplit={onSplit}
        onEnlarge={onEnlarge}
        renderBody={renderBody}
      />
    );
  }
  return (
    <div className={`split-${node.dir}`}>
      {node.children.map((child, i) => (
        <SplitView
          // eslint-disable-next-line react/no-array-index-key
          key={`${child.type === 'tile' ? child.id : `split-${i}`}`}
          node={child}
          enlarged={enlarged}
          closed={closed}
          onClose={onClose}
          onMove={onMove}
          onSplit={onSplit}
          onEnlarge={onEnlarge}
          renderBody={renderBody}
        />
      ))}
    </div>
  );
}

/** Bloomberg-style tile dock: rail-opened panels share the main area in a split tree. Tiles
 * split vertically (side by side) or horizontally (stacked) with a panel
 * picker; the rail is the single opener, so an empty dock only hints at it.
 */
export default function TileDock({ layout, enlarged, onClose, onMove, onSplit, onEnlarge, renderBody }: Props) {
  const open = flattenLayout(layout);
  if (!layout) {
    return (
      <div className="tile-dock tile-dock-empty">
        <div className="tile-empty" role="status">
          <div>All panels closed — open one from the rail on the left.</div>
        </div>
      </div>
    );
  }
  const closed = ALL_TILE_IDS.filter((id) => !open.includes(id));
  return (
    <div className={`tile-dock${enlarged ? ' has-enlarged' : ''}`}>
      <SplitView
        node={layout}
        enlarged={enlarged}
        closed={closed}
        onClose={onClose}
        onMove={onMove}
        onSplit={onSplit}
        onEnlarge={onEnlarge}
        renderBody={renderBody}
      />
    </div>
  );
}
