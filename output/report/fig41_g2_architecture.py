"""Figure 4.1 for the G2 report: technical architecture and booking-level decision workflow."""
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

NAVY, EDGE, FILL, GREY, GFILL, INK2 = '#213656', '#2d5b8c', '#e9f0f8', '#8a93a3', '#f3f4f6', '#3d4757'


def box(ax, x, y, w, h, title, lines, done=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=0.10',
                                fc=FILL if done else GFILL, ec=EDGE if done else GREY,
                                lw=1.8 if done else 1.4, ls='-' if done else (0, (4, 3))))
    ax.text(x + 0.16, y + h - 0.2, title, fontsize=11.5, fontweight='bold', color=NAVY if done else '#4b5563', va='top')
    for i, l in enumerate(lines):
        ax.text(x + 0.16, y + h - 0.62 - 0.30 * i, l, fontsize=9.0, color=INK2, va='top')


def arrow(ax, p, q, label=None, dx=0.0, dy=0.12, style='-|>'):
    ax.annotate('', q, p, arrowprops=dict(arrowstyle=style, color=EDGE, lw=1.8, shrinkA=0, shrinkB=0))
    if label:
        ax.text((p[0] + q[0]) / 2 + dx, (p[1] + q[1]) / 2 + dy, label, fontsize=8.4, color=EDGE, ha='center', va='bottom', linespacing=1.1)


fig = plt.figure(figsize=(12.2, 6.3))
ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 12.2); ax.set_ylim(0, 6.3); ax.axis('off')
W, H = 3.3, 1.55
xs = [0.25, 4.45, 8.65]
# row 1: analytics (batch, Python)
box(ax, xs[0], 4.45, W, H, 'BOOKING DATA', ['City Hotel PMS extract (real)', '+ labelled synthetic finance,', 'operations and CX tables'])
box(ax, xs[1], 4.45, W, H, 'RISK SCORING (F)', ['Python + XGBoost, weekly batch', '12,102 individual scores', '+ 1,054 group-rule scores'])
box(ax, xs[2], 4.45, W, H, 'DECISION POLICY (G1)', ['call top 15% · contact 60%', 'max 20 calls / day', '20,563 dated actions'])
arrow(ax, (xs[0] + W, 5.22), (xs[1], 5.22))
arrow(ax, (xs[1] + W, 5.22), (xs[2], 5.22))
# row 2: operations (prototype)
box(ax, xs[2], 2.25, W, H, 'OPERATIONS DASHBOARD', ['Streamlit, one fixed day (01/05/2017):', 'overview, risk queue, review, execution,', 'capacity, performance, monitoring'])
box(ax, xs[1], 2.25, W, H, 'ACTION STORE + GUARD', ['SQLite (one log per day) or PostgreSQL', 'approval, timing and capacity rules', 'in service and database triggers'])
box(ax, xs[0], 2.25, W, H, 'n8n WORKFLOW (DEMO)', ['webhook → guard → mock dispatch', 'sends nothing: executed = false', 'only accepted replies are logged'])
arrow(ax, (xs[2] + W / 2, 4.45), (xs[2] + W / 2, 2.25 + H), 'CSV / JSON\ncontract', dx=0.75, dy=-0.25)
arrow(ax, (xs[2], 3.25), (xs[1] + W, 3.25), 'propose /\napprove')
arrow(ax, (xs[1] + W, 2.65), (xs[2], 2.65), 'status,\nhistory', dy=-0.55, style='<|-')
arrow(ax, (xs[1], 3.25), (xs[0] + W, 3.25), 'checked\naction')
arrow(ax, (xs[1], 2.65), (xs[0] + W, 2.65), 'accepted\n→ log', dy=-0.55, style='<|-')
# row 3: production design
box(ax, 0.25, 0.25, 11.7, 1.3, 'PRODUCTION DESIGN (NOT BUILT)', [
    'scheduled n8n flow reading PostgreSQL · e-mail / SMS only after consent data · PMS, price and inventory changes made by staff',
    'login and roles · randomised treatment / holdout assignment before contact · intervention log → KPIs → weekly review'], done=False)
for x in (xs[0] + W / 2, xs[1] + W / 2, xs[2] + W / 2):
    ax.plot([x, x], [2.25, 1.55], color=GREY, lw=1.2, ls=(0, (4, 3)))
# legend
ax.add_patch(FancyBboxPatch((0.27, 6.02), 0.22, 0.16, boxstyle='round,pad=0,rounding_size=0.03', fc=FILL, ec=EDGE, lw=1.2))
ax.text(0.58, 6.10, 'Implemented and tested (prototype)', fontsize=8.8, color=INK2, va='center')
ax.add_patch(FancyBboxPatch((4.45, 6.02), 0.22, 0.16, boxstyle='round,pad=0,rounding_size=0.03', fc=GFILL, ec=GREY, lw=1.2, ls=(0, (3, 2))))
ax.text(4.76, 6.10, 'Design only', fontsize=8.8, color=INK2, va='center')
fig.savefig(sys.argv[1], dpi=200, facecolor='white')
