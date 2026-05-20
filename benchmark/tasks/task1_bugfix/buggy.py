"""Binary search with an off-by-one bug."""


def binary_search(arr, target):
    """Return index of target in sorted arr, or -1 if not found."""
    left = 0
    right = len(arr)  # BUG: should be len(arr) - 1

    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1

    return -1
