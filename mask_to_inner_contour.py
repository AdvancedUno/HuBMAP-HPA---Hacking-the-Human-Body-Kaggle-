def mask_to_inner_contour(mask):
	mask = mask>0.5
	pad = np.lib.pad(mask, ((1, 1), (1, 1)), 'reflect')
	contour = mask & (
			(pad[1:-1,1:-1] != pad[:-2,1:-1]) \
			| (pad[1:-1,1:-1] != pad[2:,1:-1]) \
			| (pad[1:-1,1:-1] != pad[1:-1,:-2]) \
			| (pad[1:-1,1:-1] != pad[1:-1,2:])
	)
	return contour
