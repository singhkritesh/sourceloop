// Browser notifications for new decisions. They work while the page or the installed app is open; the server can also push to a chat webhook.
const KEY = 'sl.notified';
const read = (): string[] => { try { return JSON.parse(localStorage.getItem(KEY) ?? '[]') as string[]; } catch { return []; } };
const write = (v: string[]) => { try { localStorage.setItem(KEY, JSON.stringify(v.slice(-200))); } catch { /* storage may be unavailable */ } };

export const notificationsSupported = (): boolean => typeof Notification !== 'undefined';
export const notificationsOn = (): boolean => notificationsSupported() && Notification.permission === 'granted';
export async function enableNotifications(): Promise<boolean> {
  if (!notificationsSupported()) return false;
  return (await Notification.requestPermission()) === 'granted';
}

/** Tell the person about decisions they have not been told about yet; returns how many it announced. */
export function announceNew(items: { id: string; title: string }[]): number {
  if (!notificationsOn()) return 0;
  const seen = new Set(read());
  const fresh = items.filter((i) => !seen.has(i.id));
  fresh.forEach((i) => {
    try { new Notification('A decision is waiting for you', { body: i.title, tag: i.id }); } catch { /* some mobile browsers only allow service-worker notifications */ }
  });
  if (fresh.length) write([...seen, ...fresh.map((i) => i.id)]);
  return fresh.length;
}
