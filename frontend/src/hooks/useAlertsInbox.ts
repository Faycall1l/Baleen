import { useEffect, useMemo, useState } from 'react';

import {
  filterAlerts,
  loadSeenMax,
  maxId,
  ruleOptions,
  saveSeenMax,
  unreadCount,
} from '../alertsInbox';
import type { Alert } from '../types';

// Alerts inbox state: rule/text filtering plus an unread watermark.
//
// Unread means "arrived since the inbox was last open", tracked as the max
// alert id seen with the drawer open and persisted across reloads. Opening
// the drawer marks everything current as seen — you are looking at the list.
export function useAlertsInbox(alerts: Alert[], active: boolean) {
  const [rule, setRule] = useState('all');
  const [query, setQuery] = useState('');
  const [seenMax, setSeenMax] = useState(loadSeenMax);

  useEffect(() => {
    if (active && alerts.length > 0) {
      const m = maxId(alerts);
      setSeenMax(m);
      saveSeenMax(m);
    }
  }, [active, alerts]);

  const visible = useMemo(
    () => filterAlerts(alerts, { rule, query }),
    [alerts, rule, query],
  );
  // Chip options come from the full list so an active filter never hides
  // the other rules.
  const rules = useMemo(() => ruleOptions(alerts), [alerts]);
  const unread = unreadCount(alerts, seenMax);

  return { visible, rules, rule, setRule, query, setQuery, seenMax, unread };
}
