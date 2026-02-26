git config --list# Dice Simulator with Live Bar Plot (Counts + Relative Frequency)
# --------------------------------------------------------------
# This program simulates rolling a 6-faced dice many times.
# It shows a bar chart that updates in real-time,
# with the number of times each face appeared AND
# the relative frequency (percentage).
#
# To stop the program, press Ctrl+C in the terminal window.

import random              # We need this to "roll" the dice (generate random numbers)
import matplotlib.pyplot as plt  # We need this to make the bar chart

def main():
    # The dice has 6 faces, numbered 1 to 6
    faces = [str(i) for i in range(1, 7)]  # ["1","2","3","4","5","6"]
    
    # At the start, each face has been rolled 0 times
    counts = [0] * 6  # [0, 0, 0, 0, 0, 0]

    # Turn on interactive mode in matplotlib
    plt.ion()
    fig, ax = plt.subplots()
    
    # Create the initial bar chart (all bars start at height 0)
    bars = ax.bar(faces, counts)
    ax.set_xlabel("Dice face")          # Label for the x-axis
    ax.set_ylabel("Number of rolls")    # Label for the y-axis
    ax.set_title("Live dice rolls – press Ctrl+C to stop")
    ax.set_ylim(0, 1)  # Start with y-axis up to 1 so the bars are visible

    # Add text labels on top of each bar to show count + frequency
    labels = []
    for bar in bars:
        label = ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height(),
                        "0 (0.0%)",      # Initial text
                        ha="center",
                        va="bottom")
        labels.append(label)

    # This function updates the chart after every roll
    def refresh():
        total = sum(counts)                           # Total number of rolls
        ymax = max(counts) if max(counts) > 0 else 1  # Biggest count so far
        ax.set_ylim(0, int(ymax * 1.2) + 1)           # Adjust y-axis as counts grow
        ax.set_title(f"Live dice rolls – total: {total} (Ctrl+C to stop)")

        # Update each bar and its label
        for i, bar in enumerate(bars):
            bar.set_height(counts[i])      # New bar height = new count

            # Compute relative frequency (%)
            if total > 0:
                rel_freq = (counts[i] / total) * 100
            else:
                rel_freq = 0.0

            # Update the label: "count (xx.x%)"
            labels[i].set_y(counts[i])
            labels[i].set_text(f"{counts[i]} ({rel_freq:.1f}%)")

        fig.canvas.draw()  # Redraw the figure
        plt.pause(0.05)    # Small pause so updates are visible

    # Main loop: roll the dice forever, until user presses Ctrl+C
    try:
        while True:
            roll = random.randint(1, 6)    # Pick a random number from 1 to 6
            counts[roll - 1] += 1          # Increase the count for that face
            refresh()                      # Update the chart
    except KeyboardInterrupt:
        # When the user presses Ctrl+C, stop and show the final chart
        plt.ioff()
        plt.show()

# This line makes sure the program starts when you run the file
if __name__ == "__main__":
    main()
