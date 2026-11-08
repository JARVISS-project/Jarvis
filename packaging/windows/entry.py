"""Frozen entry: divert spawn before importing Qt or composing the runtime."""
import multiprocessing

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from jarvis.runtime.packaged import main
    main()
